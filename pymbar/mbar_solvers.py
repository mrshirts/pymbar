from __future__ import division  # Ensure same division behavior in py2 and py3
import numpy as np
import math
import scipy.optimize
from pymbar.utils import ensure_type, logsumexp, check_w_normalized
import warnings

# Below are the recommended default protocols (ordered sequence of minimization algorithms / NLE solvers) for solving the MBAR equations.
# Note: we use tuples instead of lists to avoid accidental mutability.
#DEFAULT_SUBSAMPLING_PROTOCOL = (dict(method="L-BFGS-B"), )  # First use BFGS on subsampled data.
#DEFAULT_SOLVER_PROTOCOL = (dict(method="hybr"), )  # Then do fmin hybrid on full dataset.
DEFAULT_SUBSAMPLING_PROTOCOL = (dict(method="adaptive"),)  # First use BFGS on subsampled data.
DEFAULT_SOLVER_PROTOCOL = (dict(method="adaptive",),)  # Then do fmin hybrid on full dataset.


def validate_inputs(u_kn, N_k, f_k):
    """Check types and return inputs for MBAR calculations.

    Parameters
    ----------
    u_kn or q_kn : np.ndarray, shape=(n_states, n_samples), dtype='float'
        The reduced potential energies or unnormalized probabilities
    N_k : np.ndarray, shape=(n_states), dtype='int'
        The number of samples in each state
    f_k : np.ndarray, shape=(n_states), dtype='float'
        The reduced free energies of each state

    Returns
    -------
    u_kn or q_kn : np.ndarray, shape=(n_states, n_samples), dtype='float'
        The reduced potential energies or unnormalized probabilities
    N_k : np.ndarray, shape=(n_states), dtype='float'
        The number of samples in each state.  Converted to float because this cast is required when log is calculated.
    f_k : np.ndarray, shape=(n_states), dtype='float'
        The reduced free energies of each state
    """
    n_states, n_samples = u_kn.shape

    u_kn = ensure_type(u_kn, 'float', 2, "u_kn or Q_kn", shape=(n_states, n_samples))
    N_k = ensure_type(N_k, 'float', 1, "N_k", shape=(n_states,), warn_on_cast=False)  # Autocast to float because will be eventually used in float calculations.
    f_k = ensure_type(f_k, 'float', 1, "f_k", shape=(n_states,))

    return u_kn, N_k, f_k


def self_consistent_update(u_kn, N_k, f_k):
    """Return an improved guess for the dimensionless free energies

    Parameters
    ----------
    u_kn : np.ndarray, shape=(n_states, n_samples), dtype='float'
        The reduced potential energies, i.e. -log unnormalized probabilities
    N_k : np.ndarray, shape=(n_states), dtype='int'
        The number of samples in each state
    f_k : np.ndarray, shape=(n_states), dtype='float'
        The reduced free energies of each state

    Returns
    -------
    f_k : np.ndarray, shape=(n_states), dtype='float'
        Updated estimate of f_k

    Notes
    -----
    Equation C3 in MBAR JCP paper.
    """

    u_kn, N_k, f_k = validate_inputs(u_kn, N_k, f_k)
    
    states_with_samples = (N_k > 0)

    # Only the states with samples can contribute to the denominator term.
    log_denominator_n = logsumexp(f_k[states_with_samples] - u_kn[states_with_samples].T, b=N_k[states_with_samples], axis=1)
    
    # All states can contribute to the numerator term.
    return -1. * logsumexp(-log_denominator_n - u_kn, axis=1)



def mbar_gradient(u_kn, N_k, f_k):
    """Gradient of MBAR objective function.

    Parameters
    ----------
    u_kn : np.ndarray, shape=(n_states, n_samples), dtype='float'
        The reduced potential energies, i.e. -log unnormalized probabilities
    N_k : np.ndarray, shape=(n_states), dtype='int'
        The number of samples in each state
    f_k : np.ndarray, shape=(n_states), dtype='float'
        The reduced free energies of each state

    Returns
    -------
    grad : np.ndarray, dtype=float, shape=(n_states)
        Gradient of mbar_objective

    Notes
    -----
    This is equation C6 in the JCP MBAR paper.
    """
    u_kn, N_k, f_k = validate_inputs(u_kn, N_k, f_k)

    log_denominator_n = logsumexp(f_k - u_kn.T, b=N_k, axis=1)
    log_numerator_k = logsumexp(-log_denominator_n - u_kn, axis=1)
    return -1 * N_k * (1.0 - np.exp(f_k + log_numerator_k))


def mbar_objective_and_gradient(u_kn, N_k, f_k):
    """Calculates both objective function and gradient for MBAR.

    Parameters
    ----------
    u_kn : np.ndarray, shape=(n_states, n_samples), dtype='float'
        The reduced potential energies, i.e. -log unnormalized probabilities
    N_k : np.ndarray, shape=(n_states), dtype='int'
        The number of samples in each state
    f_k : np.ndarray, shape=(n_states), dtype='float'
        The reduced free energies of each state


    Returns
    -------
    obj : float
        Objective function
    grad : np.ndarray, dtype=float, shape=(n_states)
        Gradient of objective function

    Notes
    -----
    This objective function is essentially a doubly-summed partition function and is
    quite sensitive to precision loss from both overflow and underflow. For optimal
    results, u_kn can be preconditioned by subtracting out a `n` dependent
    vector.

    More optimal precision, the objective function uses math.fsum for the
    outermost sum and logsumexp for the inner sum.
    
    The gradient is equation C6 in the JCP MBAR paper; the objective
    function is its integral.
    """
    u_kn, N_k, f_k = validate_inputs(u_kn, N_k, f_k)

    log_denominator_n = logsumexp(f_k - u_kn.T, b=N_k, axis=1)
    log_numerator_k = logsumexp(-log_denominator_n - u_kn, axis=1)
    grad = -1 * N_k * (1.0 - np.exp(f_k + log_numerator_k))

    obj = math.fsum(log_denominator_n) - N_k.dot(f_k)

    return obj, grad


def mbar_hessian(u_kn, N_k, f_k):
    """Hessian of MBAR objective function.

    Parameters
    ----------
    u_kn : np.ndarray, shape=(n_states, n_samples), dtype='float'
        The reduced potential energies, i.e. -log unnormalized probabilities
    N_k : np.ndarray, shape=(n_states), dtype='int'
        The number of samples in each state
    f_k : np.ndarray, shape=(n_states), dtype='float'
        The reduced free energies of each state

    Returns
    -------
    H : np.ndarray, dtype=float, shape=(n_states, n_states)
        Hessian of mbar objective function.

    Notes
    -----
    Equation (C9) in JCP MBAR paper.
    """
    u_kn, N_k, f_k = validate_inputs(u_kn, N_k, f_k)

    W = mbar_W_nk(u_kn, N_k, f_k)

    H = W.T.dot(W)
    H *= N_k
    H *= N_k[:, np.newaxis]
    H -= np.diag(W.sum(0) * N_k)

    return -1.0 * H


def mbar_log_W_nk(u_kn, N_k, f_k):
    """Calculate the log weight matrix.

    Parameters
    ----------
    u_kn : np.ndarray, shape=(n_states, n_samples), dtype='float'
        The reduced potential energies, i.e. -log unnormalized probabilities
    N_k : np.ndarray, shape=(n_states), dtype='int'
        The number of samples in each state
    f_k : np.ndarray, shape=(n_states), dtype='float'
        The reduced free energies of each state

    Returns
    -------
    logW_nk : np.ndarray, dtype='float', shape=(n_samples, n_states)
        The normalized log weights.

    Notes
    -----
    Equation (9) in JCP MBAR paper.
    """
    u_kn, N_k, f_k = validate_inputs(u_kn, N_k, f_k)

    log_denominator_n = logsumexp(f_k - u_kn.T, b=N_k, axis=1)
    logW = f_k - u_kn.T - log_denominator_n[:, np.newaxis]
    return logW

def mbar_W_nk(u_kn, N_k, f_k):
    """Calculate the weight matrix.

    Parameters
    ----------
    u_kn : np.ndarray, shape=(n_states, n_samples), dtype='float'
        The reduced potential energies, i.e. -log unnormalized probabilities
    N_k : np.ndarray, shape=(n_states), dtype='int'
        The number of samples in each state
    f_k : np.ndarray, shape=(n_states), dtype='float'
        The reduced free energies of each state

    Returns
    -------
    W_nk : np.ndarray, dtype='float', shape=(n_samples, n_states)
        The normalized weights.

    Notes
    -----
    Equation (9) in JCP MBAR paper.
    """
    return np.exp(mbar_log_W_nk(u_kn, N_k, f_k))

def adaptive(u_kn, N_k, f_k, tol = 1.0e-12, options = None):

    """
    Determine dimensionless free energies by a combination of Newton-Raphson iteration and self-consistent iteration.
    Picks whichever method gives the lowest gradient.
    Is slower than NR since it calculates the log norms twice each iteration.

    OPTIONAL ARGUMENTS
    tol (float between 0 and 1) - relative tolerance for convergence (default 1.0e-12)

    options: dictionary of options
        gamma (float between 0 and 1) - incrementor for NR iterations (default 1.0).  Usually not changed now, since adaptively switch.
        maximum_iterations (int) - maximum number of Newton-Raphson iterations (default 250: either NR converges or doesn't, pretty quickly)
        verbose (boolean) - verbosity level for debug output

    NOTES


    This method determines the dimensionless free energies by
    minimizing a convex function whose solution is the desired
    estimator.  The original idea came from the construction of a
    likelihood function that independently reproduced the work of
    Geyer (see [1] and Section 6 of [2]).  This can alternatively be
    formulated as a root-finding algorithm for the Z-estimator.  More
    details of this procedure will follow in a subsequent paper.  Only
    those states with nonzero counts are include in the estimation
    procedure.

    REFERENCES
    See Appendix C.2 of [1].

    """
    # put the defaults here in case we get passed an 'options' dictionary that is only partial
    options.setdefault('verbose',False)
    options.setdefault('maximum_iterations',250)
    options.setdefault('print_warning',False)
    options.setdefault('gamma',1.0)

    gamma = options['gamma']
    doneIterating = False
    if options['verbose'] == True:
        print("Determining dimensionless free energies by Newton-Raphson / self-consistent iteration.")

    import pdb
    pdb.set_trace()
    test1 = mbar_W_nk(u_kn, N_k, f_k)

    if tol < 1.5e-15:
        print("Tolerance may be too close to machine precision to converge.")
    # keep track of Newton-Raphson and self-consistent iterations
    nr_iter = 0
    sci_iter = 0

    f_sci = np.zeros(len(f_k), dtype=np.float64)
    f_nr = np.zeros(len(f_k), dtype=np.float64)

    # Perform Newton-Raphson iterations (with sci computed on the way)
    for iteration in range(0, options['maximum_iterations']):
        g = mbar_gradient(u_kn, N_k, f_k)  # Objective function gradient
        H = mbar_hessian(u_kn, N_k, f_k)  # Objective function hessian
        Hinvg = np.linalg.lstsq(H, g, rcond=-1)[0]
        Hinvg -= Hinvg[0]
        f_nr = f_k - gamma * Hinvg

        # self-consistent iteration gradient norm and saved log sums.
        f_sci = self_consistent_update(u_kn, N_k, f_k)
        f_sci = f_sci -  f_sci[0]   # zero out the minimum
        g_sci = mbar_gradient(u_kn, N_k, f_sci)
        gnorm_sci = np.dot(g_sci, g_sci)

        # newton raphson gradient norm and saved log sums.
        g_nr = mbar_gradient(u_kn, N_k, f_nr)
        gnorm_nr = np.dot(g_nr, g_nr)

        # we could save the gradient, for the next round, but it's not too expensive to
        # compute since we are doing the Hessian anyway.

        if options['verbose']:
            print("self consistent iteration gradient norm is %10.5g, Newton-Raphson gradient norm is %10.5g" % (gnorm_sci, gnorm_nr))
        # decide which directon to go depending on size of gradient norm
        f_old = f_k
        if (gnorm_sci < gnorm_nr or sci_iter < 2):
            f_k = f_sci
            sci_iter += 1
            if options['verbose']:
                if sci_iter < 2:
                    print("Choosing self-consistent iteration on iteration %d" % iteration)
                else:
                    print("Choosing self-consistent iteration for lower gradient on iteration %d" % iteration)
        else:
            f_k = f_nr
            nr_iter += 1
            if options['verbose']:
                print("Newton-Raphson used on iteration %d" % iteration)

        # routine changes them.
        max_delta = np.max(np.abs(f_k[1:]-f_old[1:]))/np.max(np.abs(f_k[1:]))
        if np.isnan(max_delta) or (max_delta < tol):
            doneIterating = True
            break

    if doneIterating:
        if options['verbose']:
            print('Converged to tolerance of {:e} in {:d} iterations.'.format(max_delta, iteration + 1))
            print('Of {:d} iterations, {:d} were Newton-Raphson iterations and {:d} were self-consistent iterations'.format(iteration + 1, nr_iter, sci_iter))
            if np.all(f_k == 0.0):
                # all f_k appear to be zero
                print('WARNING: All f_k appear to be zero.')
    else:
        print('WARNING: Did not converge to within specified tolerance.')
        print('max_delta = {:e}, tol = {:e}, maximum_iterations = {:d}, iterations completed = {:d}'.format(max_delta,tol, options['maximum_iterations'], iteration))
    return f_k

def precondition_u_kn(u_kn, N_k, f_k):
    """Subtract a sample-dependent constant from u_kn to improve precision

    Parameters
    ----------
    u_kn : np.ndarray, shape=(n_states, n_samples), dtype='float'
        The reduced potential energies, i.e. -log unnormalized probabilities
    N_k : np.ndarray, shape=(n_states), dtype='int'
        The number of samples in each state
    f_k : np.ndarray, shape=(n_states), dtype='float'
        The reduced free energies of each state

    Returns
    -------
    u_kn : np.ndarray, shape=(n_states, n_samples), dtype='float'
        The reduced potential energies, i.e. -log unnormalized probabilities

    Notes
    -----
    Returns u_kn - x_n, where x_n is based on the current estimate of f_k.
    Upon subtraction of x_n, the MBAR objective function changes by an
    additive constant, but its derivatives remain unchanged.  We choose
    x_n such that the current objective function value is zero, which
    should give maximum precision in the objective function.
    """
    u_kn, N_k, f_k = validate_inputs(u_kn, N_k, f_k)
    u_kn = u_kn - u_kn.min(0)
    u_kn += (logsumexp(f_k - u_kn.T, b=N_k, axis=1)) - N_k.dot(f_k) / float(N_k.sum())
    return u_kn


def solve_mbar_once(u_kn_nonzero, N_k_nonzero, f_k_nonzero, method="hybr", tol=1E-12, options=None):
    """Solve MBAR self-consistent equations using some form of equation solver.

    Parameters
    ----------
    u_kn_nonzero : np.ndarray, shape=(n_states, n_samples), dtype='float'
        The reduced potential energies, i.e. -log unnormalized probabilities
        for the nonempty states
    N_k_nonzero : np.ndarray, shape=(n_states), dtype='int'
        The number of samples in each state for the nonempty states
    f_k_nonzero : np.ndarray, shape=(n_states), dtype='float'
        The reduced free energies for the nonempty states
    method : str, optional, default="hybr"
        The optimization routine to use.  This can be any of the methods
        available via scipy.optimize.minimize() or scipy.optimize.root().
    tol : float, optional, default=1E-14
        The convergance tolerance for minimize() or root()
    verbose: bool
        Whether to print information about the solution method.
    options: dict, optional, default=None
        Optional dictionary of algorithm-specific parameters.  See
        scipy.optimize.root or scipy.optimize.minimize for details.

    Returns
    -------
    f_k : np.ndarray
        The converged reduced free energies.
    results : dict
        Dictionary containing entire results of optimization routine, may
        be useful when debugging convergence.

    Notes
    -----
    This function requires that N_k_nonzero > 0--that is, you should have
    already dropped all the states for which you have no samples.
    Internally, this function works in a reduced coordinate system defined
    by subtracting off the first component of f_k and fixing that component
    to be zero.

    For fast but precise convergence, we recommend calling this function
    multiple times to polish the result.  `solve_mbar()` facilitates this.
    """
    u_kn_nonzero, N_k_nonzero, f_k_nonzero = validate_inputs(u_kn_nonzero, N_k_nonzero, f_k_nonzero)
    f_k_nonzero = f_k_nonzero - f_k_nonzero[0]  # Work with reduced dimensions with f_k[0] := 0
    u_kn_nonzero = precondition_u_kn(u_kn_nonzero, N_k_nonzero, f_k_nonzero)

    pad = lambda x: np.pad(x, (1, 0), mode='constant')  # Helper function inserts zero before first element
    unpad_second_arg = lambda obj, grad: (obj, grad[1:])  # Helper function drops first element of gradient

    # Create objective functions / nonlinear equations to send to scipy.optimize, fixing f_0 = 0
    grad = lambda x: mbar_gradient(u_kn_nonzero, N_k_nonzero, pad(x))[1:]  # Objective function gradient
    grad_and_obj = lambda x: unpad_second_arg(*mbar_objective_and_gradient(u_kn_nonzero, N_k_nonzero, pad(x)))  # Objective function gradient and objective function
    hess = lambda x: mbar_hessian(u_kn_nonzero, N_k_nonzero, pad(x))[1:][:, 1:]  # Hessian of objective function

    with warnings.catch_warnings(record=True) as w:
        if method in ["L-BFGS-B", "dogleg", "CG", "BFGS", "Newton-CG", "TNC", "trust-ncg", "SLSQP"]:
            if method in ["L-BFGS-B", "CG"]:
                hess = None  # To suppress warning from passing a hessian function.
            results = scipy.optimize.minimize(grad_and_obj, f_k_nonzero[1:], jac=True, hess=hess, method=method, tol=tol, options=options)
            f_k_nonzero = pad(results["x"])
        elif method == 'adaptive':
            results = adaptive(u_kn_nonzero, N_k_nonzero, f_k_nonzero, tol=tol, options=options)
            f_k_nonzero = results # they are the same for adaptive, until we decide to return more.
        else:
            results = scipy.optimize.root(grad, f_k_nonzero[1:], jac=hess, method=method, tol=tol, options=options)
            f_k_nonzero = pad(results["x"])

    #If there were runtime warnings, show the messages
    if len(w) > 0:
        for warn_msg in w:
            warnings.showwarning(warn_msg.message, warn_msg.category, warn_msg.filename, warn_msg.lineno, warn_msg.file, "") 
        #Ensure MBAR solved correctly
        W_nk_check = mbar_W_nk(u_kn_nonzero, N_k_nonzero, f_k_nonzero)
        check_w_normalized(W_nk_check, N_k_nonzero)
        print("MBAR weights converged within tolerance, despite the SciPy Warnings. Please validate your results.")
       
            
    return f_k_nonzero, results


def solve_mbar(u_kn_nonzero, N_k_nonzero, f_k_nonzero, solver_protocol=None):
    """Solve MBAR self-consistent equations using some sequence of equation solvers.

    Parameters
    ----------
    u_kn_nonzero : np.ndarray, shape=(n_states, n_samples), dtype='float'
        The reduced potential energies, i.e. -log unnormalized probabilities
        for the nonempty states
    N_k_nonzero : np.ndarray, shape=(n_states), dtype='int'
        The number of samples in each state for the nonempty states
    f_k_nonzero : np.ndarray, shape=(n_states), dtype='float'
        The reduced free energies for the nonempty states
    solver_protocol: tuple(dict()), optional, default=None
        Optional list of dictionaries of steps in solver protocol.
        If None, a default protocol will be used.

    Returns
    -------
    f_k : np.ndarray
        The converged reduced free energies.
    all_results : list(dict())
        List of results from each step of solver_protocol.  Each element in
        list contains the results dictionary from solve_mbar_once()
        for the corresponding step.

    Notes
    -----
    This function requires that N_k_nonzero > 0--that is, you should have
    already dropped all the states for which you have no samples.
    Internally, this function works in a reduced coordinate system defined
    by subtracting off the first component of f_k and fixing that component
    to be zero.

    This function calls `solve_mbar_once()` multiple times to achieve
    converged results.  Generally, a single call to solve_mbar_once()
    will not give fully converged answers because of limited numerical precision.
    Each call to `solve_mbar_once()` re-conditions the nonlinear
    equations using the current guess.
    """
    if solver_protocol is None:
        solver_protocol = DEFAULT_SOLVER_PROTOCOL

    all_results = []
    for k, options in enumerate(solver_protocol):
        f_k_nonzero, results = solve_mbar_once(u_kn_nonzero, N_k_nonzero, f_k_nonzero, **options)
        all_results.append(results)
        all_results.append(("Final gradient norm: %.3g" % np.linalg.norm(mbar_gradient(u_kn_nonzero, N_k_nonzero, f_k_nonzero))))
    return f_k_nonzero, all_results


def subsample_data(u_kn0, N_k0, s_n, subsampling, rescale=False, replace=False):
    """Return a subsample from dataset.

    Parameters
    ----------
    u_kn0 : np.ndarray, shape=(n_states, n_samples), dtype='float'
        The reduced potential energies, i.e. -log unnormalized probabilities
    N_k0 : np.ndarray, shape=(n_states), dtype='int'
        The number of samples in each state
    s_n : np.ndarray, shape=(n_samples), dtype='int'
        State of origin of each sample x_n
    subsampling : int
        The factor by which to subsample (E.g. 10 for 10X).
    rescale : bool, optional, default=True
        If True, rescale and shift the subset to have same mean and variance
        as full dataset
    replace : bool, optional, default=False
        Subsample with replacement

    Returns
    -------
    u_kn : np.ndarray, shape=(n_states, n_samples), dtype='float'
        The reduced potential energies, i.e. -log unnormalized probabilities
    N_k : np.ndarray, shape=(n_states), dtype='float'
        The new number of samples in each state

    Notes
    -----
    In situations where N >> K and the overlap is good, one might use
    subsampling to solve MBAR on a smaller dataset as an initial guess.
    """    
    n_states = len(N_k0)
    N_k = N_k0 // subsampling
    N_k[(N_k == 0) & (N_k0 > 0)] = 1

    u_kn = np.zeros((n_states, N_k.sum()))

    if rescale:
        mu_k = np.array([u_kn0[:, s_n == k].mean(1) for k in range(n_states)])
        sigma_k = np.array([u_kn0[:, s_n == k].std(1) for k in range(n_states)])
        standardize = lambda x: (x - x.mean(1)[:, np.newaxis]) / x.std(1)[:, np.newaxis]
    else:
        mu_k = np.zeros((n_states, n_states))
        sigma_k = np.ones((n_states, n_states))
        standardize = lambda x: x

    start = 0
    for k in range(n_states):
        if N_k[k] <= 0:
            continue
        samples = np.random.choice(np.where(s_n == k)[0], size=(N_k[k].astype(int)), replace=replace)
        u_k = standardize(u_kn0[:, samples]) * sigma_k[k][:, np.newaxis] + mu_k[k][:, np.newaxis]
        num = N_k[k]
        u_kn[:, start:start + num] = u_k
        start += num

    return u_kn, N_k


def solve_mbar_with_subsampling(u_kn, N_k, f_k, solver_protocol, subsampling_protocol, subsampling, x_kindices=None):
    """Solve for free energies of states with samples, then calculate for
    empty states.  Optionally uses subsampling as a hot-start to speed up
    calculations.

    Parameters
    ----------
    u_kn : np.ndarray, shape=(n_states, n_samples), dtype='float'
        The reduced potential energies, i.e. -log unnormalized probabilities
    N_k : np.ndarray, shape=(n_states), dtype='int'
        The number of samples in each state
    f_k : np.ndarray, shape=(n_states), dtype='float'
        The reduced free energies of each state
    solver_protocol: tuple(dict()), optional, default=None
        Sequence of dictionaries of steps in solver protocol for final
        stage of refinement.
    subsampling_protocol: tuple(dict()), optional, default=None
        Sequence of dictionaries of steps in solver protocol for first
        stage of refinement with subsampled dataset.
    subsampling : int
        By what factor do we subsample the dataset for getting a first
        pass solution to MBAR.
    x_kindices : np.ndarray, optional, shape=(N_samples), dtype='int'
        The stage of origin for each sample.  This is required to use
        subsampling to use a fast guess as way to hot start and accelerate
        MBAR.

    Returns
    -------
    f_k : np.ndarray, shape=(n_states), dtype='float'
        The free energies of states
    
    
    """
    states_with_samples = np.where(N_k > 0)[0]

    if len(states_with_samples) == 1:
        f_k_nonzero = np.array([0.0])
    else:
        if subsampling is not None and x_kindices is not None and subsampling > 1:
            s_n = np.unique(x_kindices, return_inverse=True)[1]
            u_kn_subsampled, N_k_subsampled = subsample_data(u_kn[states_with_samples], N_k[states_with_samples], s_n, subsampling=subsampling)
            f_k_nonzero, all_results = solve_mbar(u_kn_subsampled, N_k_subsampled, f_k[states_with_samples], solver_protocol=subsampling_protocol)
        else:
            f_k_nonzero, all_results = solve_mbar(u_kn[states_with_samples], N_k[states_with_samples], f_k[states_with_samples], solver_protocol=subsampling_protocol)

        f_k[states_with_samples] = f_k_nonzero
        f_k_nonzero, all_results = solve_mbar(u_kn[states_with_samples], N_k[states_with_samples], f_k[states_with_samples], solver_protocol=solver_protocol)

    f_k[states_with_samples] = f_k_nonzero

    # Update all free energies because those from states with zero samples are not correctly computed by Newton-Raphson.
    f_k = self_consistent_update(u_kn, N_k, f_k)
    f_k -= f_k[0]  # This is necessary because state 0 might have had zero samples, but we still want that state to be the reference with free energy 0.

    return f_k


def solve_mbar_with_smearing(u_kn, N_k, f_k, solver_protocol, binning_protocol, x_kindices=None):

    """bin the u_kn to reduce the number of samples.

    Parameters
    ----------
    u_kn : np.ndarray, shape=(n_states, n_samples), dtype='float'
        The reduced potential energies, i.e. -log unnormalized probabilities
    N_k : np.ndarray, shape=(n_states), dtype='int'
        The number of samples in each state
    f_k : np.ndarray, shape=(n_states), dtype='float'
        The reduced free energies of each state
    solver_protocol: tuple(dict()), optional, default=None
        Sequence of dictionaries of steps in solver protocol for final
        stage of refinement.
    binning_protocol: tuple(dict()), optional, default=None
        Sequence of dictionaries of steps in solver protocol for first
        stage of refinement with binned dataset.

    Returns
    -------
    f_k : np.ndarray, shape=(n_states), dtype='float'
        The free energies of states

    Theory: take N distributions p_i(x) = exp(f_i-u_i(x)).
    Approximate the x by histogramming, so we have b bins in energy,
    so that each u_i(x) = u_ib, with the occupancy of each bin
    changing with each i. We denote the occupancy as N_i(b).
    
    We then approximate p_i(x) by the discrete distribution q_i(x_b) =
    N_i(b) exp(f_i - u_i(x_b))). Then the mixture distribution will be
    q_m(x_b) = N \sum_i n_i q_i(x_b). 

    We then want to perform importance sampling from the mixture
    distribution. 

    <O>_i = \sum_m O q_i(x_b) / \sum_k (n_k/N) q_k(x_b)

    Then replace with 1 to get normalization. In this case, we
    actually have the full discrete distribution.

    1 = \int q_i(x_b) / \sum_k (n_k/N) q_k(x_b)
    1 = \int N_i(x_b) exp(f_i - u_i(x_b)) / \sum_k (n_k/N) N_k(x_b) q_k(x_b)

    Two ways of doing the binning.  Pick a single x_b that we assume
    is representative from each subsample. But isn't that just the
    same as subsampling? The only difference is in making sure that we
    have bridging samples for all states.

    The second is to come up with a binning matrix.  We can have a
    conditional matrix that if state I has energy N_bi, then what is
    the chance that state K has N_bj?

    exp(-f_i) = \int N_i(x_b) exp(- u_i(x_b)) / \sum_k (n_k/N) N_k(x_b) q_k(x_b)
    exp(-f_i) = \int N_i(x_b) exp(- u_i(x_b)) / \sum_k (n_k/N) N_k(x_b) exp(f_k u_k(x_b) 
    exp(-f_i) = \int exp(- u_i(x_b) + ln N_i(x_b)) / \sum_k (n_k/N) N_k(x_b) exp(f_k - u_k(x_b) + ln N_k(x_b))

    We then first need to generate an estimate of what u_k(x_b) is
    when x_b is binned. The main issue is that we don't have the x_b
    necessarily - all we know is the correlation beween the different
    values of u_k.  If we bin over the entire range, then u_k(x_b)
    will be defined for all states.

    exp(-f_i) = \sum_b N_b exp(-u_i(x_b)) / \sum_k (n_k/N) exp(f_k - u_k(x_b))

    or if we do it in terms of a mixture distribution: 

    exp(-f_i) = \sum_b N_i(b) exp(-u_i(x_b)) / \sum_k (n_k/N) N_k(b) exp(f_k - u_k(x_b))

    So the N_i(b) will be the occupancy of each bin.  But the occupancy is the same; it's the energy of each bin
    that is the issue.

    """
    states_with_samples = np.where(N_k > 0)[0]

    # come up with a good algorithm for picking bins?


    ### Try: tweaked subsampling
    ## what we should see when we look at state K is lack of an energy gap if we histogram all the energies
    ## TODO: test to see if this is the case.

    N_k_samples = len(N_k[states_with_samples])
    ## we need to identify the matrix of overlaps.  We look over the pairwise states, and see if there is overlap. 
    sum_Nk = np.zeros(N_k_samples)+1)
    for i in range(N_k_samples):
        sum_Nk[i+1] = sum_Nk[i] + N_k[states_with_samples[i]]
    
    minmax = np.zeros([2,N_k_samples,N_k_samples]
    # generate a matrix of overlaps (From min/max)
    for i in range(len(N_k)):
        binnum = np.min(100,N_k[i])
        irange = sum_Nk[i]:sum_N[i+1]
        for j in range(len(N_k)):
            d1 = np.min(u_kn[irange,j])

    for i in range(len(N_k)):
        d1 = np.histogram(u_kn[i,:],bins=20*len(N_k))
    nbins = 10*len(N_k)
    if len(states_with_samples) == 1:
        f_k_nonzero = np.array([0.0])
    else:
        # first find out the width of bin in energy to use.
        bin_k = np.zeros([len(states_with_samples),nbins],dtype=int)
        bin_ukn = np.zeros(np.shape(u_kn))
        # for now, min and max everywhere.
        umax = np.max(u_kn)
        umin = np.min(u_kn)
        percents = np.linspace(0,100,nbins+1)
        bins = np.percentile(u_kn,percents)  # bins with even sampling over the entire energy range.
        bins[-1] *= (1+10E-04) # make it just a bit bigger so nothing out of range.
        indices = np.digitize(u_kn,bins) # which indices are each sample from?
        u_kn_smeared = np.zeros([len(N_k),nbins])
        nbin_sum = 0
        for i in range(nbins):
            bool_locations = indices==(i+1)
            sum_bin = np.sum(bool_locations)
            rn = np.random.randint(sum_bin)
            # now, pick random samples from each bin.
            locarray = np.where(bool_locations)
            sel = locarray[1][rn]
            hnbin = int(sum_bin/len(N_k))
            #u_kn_smeared[:,nbin_sum:nbin_sum+hnbin] = np.tile(u_kn[:,sel],(hnbin,1)).transpose()
            u_kn_smeared[:,i] = u_kn[:,sel]

        # basic idea: we replace N samples with B bins, each with N_b samples, with \sum B N_b = N.
        # How is this different from subsampling? We can choose the bins to ensure overlap, at the cost of accuracy.
        # In theory, we could repeat with finer bins a couple of iterations.
        # To make the statistical mechanics most straightforward, we want to make sure that 
        import pdb
        pdb.set_trace()

        Ntot = np.sum(N_k)
        f_k_nonzero, all_results = solve_mbar(u_kn_smeared,(nbins/Ntot)*N_k[states_with_samples],f_k[states_with_samples],solver_protocol=binning_protocol)
        import pdb
        pdb.set_trace()
        f_k[states_with_samples] = f_k_nonzero
        f_k_nonzero, all_results = solve_mbar(u_kn[states_with_samples], N_k[states_with_samples], f_k[states_with_samples], solver_protocol=solver_protocol)

    f_k[states_with_samples] = f_k_nonzero

    # Update all free energies because those from states with zero samples are not correctly computed by Newton-Raphson.
    f_k = self_consistent_update(u_kn, N_k, f_k)
    f_k -= f_k[0]  # This is necessary because state 0 might have had zero samples, but we still want that state to be the reference with free energy 0.

    return f_k
