"""
Fitter for pairwise state tomography
"""

from ast import literal_eval
from itertools import combinations, product

import numpy as np
import scipy.optimize

from .pairwise_state_tomography_circuits import _get_qubits

# Two-qubit Pauli matrices
_PAULIS = {
    'I': np.array([[1, 0], [0, 1]], dtype=complex),
    'X': np.array([[0, 1], [1, 0]], dtype=complex),
    'Y': np.array([[0, -1j], [1j, 0]], dtype=complex),
    'Z': np.array([[1, 0], [0, -1]], dtype=complex),
}

# Eigenvalue maps for two-qubit marginal bitstrings.
# Convention: first char = qubit j (more significant), second char = qubit i.
_OBSERVABLE_FIRST = {'00': 1, '01': -1, '10': 1, '11': -1}       # Z_i
_OBSERVABLE_SECOND = {'00': 1, '01': 1, '10': -1, '11': -1}      # Z_j
_OBSERVABLE_CORRELATED = {'00': 1, '01': -1, '10': -1, '11': 1}  # Z_i Z_j


def _marginal_counts(counts, qubits):
    """
    Marginalize a counts dict to the specified qubit indices.

    Qiskit bitstrings are big-endian with qubit 0 at the rightmost position.
    The returned 2-bit keys are ordered (qubit_j, qubit_i) i.e. the higher
    index is the more significant (leftmost) bit.
    """
    marginal = {}
    for bitstring, count in counts.items():
        bitstring = bitstring.replace(' ', '')
        n = len(bitstring)
        bits = ''.join(bitstring[n - 1 - q] for q in sorted(qubits, reverse=True))
        marginal[bits] = marginal.get(bits, 0) + count
    return marginal


def _average_data(counts, observable):
    """Weighted average of eigenvalues over measurement outcomes."""
    total = sum(counts.values())
    if total == 0:
        return 0.0
    return sum(counts.get(k, 0) * v / total for k, v in observable.items())


def _linear_inversion(expectation_values):
    """
    Reconstruct a two-qubit density matrix from Pauli expectation values via
    linear inversion.

    The density matrix is expressed in the qubit-j ⊗ qubit-i Kronecker basis
    (j more significant), so the Pauli expansion is:

        rho = (1/4) sum_{a,b} <sigma_a^i sigma_b^j> kron(sigma_b, sigma_a)

    Args:
        expectation_values (dict): maps (basis_i, basis_j) -> float for all
            non-trivial Pauli pairs. (I, I) is not required.

    Returns:
        np.ndarray: 4x4 complex density matrix (not guaranteed to be PSD).
    """
    rho = np.kron(_PAULIS['I'], _PAULIS['I']).astype(complex)
    for (a, b), val in expectation_values.items():
        rho += val * np.kron(_PAULIS[b], _PAULIS[a])
    return rho / 4


def _project_psd(rho):
    """
    Project to the nearest positive-semidefinite, trace-1 Hermitian matrix
    (Frobenius norm).
    """
    rho = (rho + rho.conj().T) / 2
    vals, vecs = np.linalg.eigh(rho)
    vals = np.maximum(vals, 0)
    total = vals.sum()
    if total > 0:
        vals /= total
    return vecs @ np.diag(vals) @ vecs.conj().T


def _params_to_rho(params):
    """
    Map the 16-element Cholesky parameter vector to a valid density matrix.

    Parameterization: rho = T T† / Tr(T T†) where T is lower triangular with
    real non-negative diagonal.
    """
    t = np.zeros((4, 4), dtype=complex)
    idx = 0
    for i in range(4):
        t[i, i] = params[idx]
        idx += 1
        for j in range(i):
            t[i, j] = params[idx] + 1j * params[idx + 1]
            idx += 2
    rho = t @ t.conj().T
    tr = np.real(np.trace(rho))
    return rho / tr if tr > 1e-10 else rho


def _mle(data):
    """
    Reconstruct a two-qubit density matrix via maximum likelihood estimation.

    Uses the Cholesky parameterization rho = T T† / Tr(T T†) and scipy's
    L-BFGS-B optimizer.

    Args:
        data (dict): maps (basis_i, basis_j) -> marginal counts dict.

    Returns:
        np.ndarray: 4x4 complex density matrix.
    """
    outcomes = ['00', '01', '10', '11']

    # Pre-compute POVM elements: kron(M_bj^{b1}, M_bi^{b0})
    # first char b1 = qubit j, second char b0 = qubit i
    povm = {}
    for (bi, bj) in data:
        for outcome in outcomes:
            b1, b0 = int(outcome[0]), int(outcome[1])
            s1 = 1 - 2 * b1  # +1 eigenvalue if 0, -1 if 1
            s0 = 1 - 2 * b0
            mj = (np.eye(2) + s1 * _PAULIS[bj]) / 2
            mi = (np.eye(2) + s0 * _PAULIS[bi]) / 2
            povm[(bi, bj, outcome)] = np.kron(mj, mi)

    def neg_log_likelihood(params):
        rho = _params_to_rho(params)
        ll = 0.0
        for (bi, bj), counts in data.items():
            for outcome, n in counts.items():
                if n == 0:
                    continue
                p = max(np.real(np.trace(povm[(bi, bj, outcome)] @ rho)), 1e-12)
                ll -= n * np.log(p)
        return ll

    # Diagonal elements of T (indices 0, 1, 4, 9) must be non-negative
    diag_indices = {0, 1, 4, 9}
    bounds = [(0, None) if i in diag_indices else (None, None) for i in range(16)]

    x0 = np.zeros(16)
    x0[0] = x0[1] = x0[4] = x0[9] = 0.5  # start from maximally mixed

    res = scipy.optimize.minimize(
        neg_log_likelihood, x0, method='L-BFGS-B', bounds=bounds
    )
    return _params_to_rho(res.x)


class PairwiseStateTomographyFitter:
    """
    Fitter for pairwise state tomography.

    Takes the Result from running the circuits returned by
    pairwise_state_tomography_circuits and reconstructs the two-qubit reduced
    density matrix (or Pauli expectation values) for every pair of measured
    qubits.
    """

    def __init__(self, result, circuits, measured_qubits):
        """
        Args:
            result: Qiskit Result object from executing the tomography circuits.
            circuits (list): the circuit list returned by
                pairwise_state_tomography_circuits.
            measured_qubits: the qubits that were measured, passed in the same
                form as to pairwise_state_tomography_circuits.
        """
        self._result = result
        self._circuits = circuits
        self._qubit_list = _get_qubits(measured_qubits)

    def fit(self, method='lstsq', pairs_list=None, output='density_matrix', **kwargs):
        """
        Reconstruct pairwise quantum states.

        Args:
            method (str): 'lstsq' (default) for linear inversion followed by
                projection to the nearest physical density matrix, or 'mle'
                for maximum likelihood estimation via scipy.
            pairs_list (list[tuple]): pairs of indices into measured_qubits to
                fit. Defaults to all C(N, 2) pairs.
            output (str): 'density_matrix' (default) or 'expectation'.

        Returns:
            With output='density_matrix': dict mapping (i, j) -> 4x4 complex
            ndarray.

            With output='expectation': unified dict with two kinds of keys:
              - Integer key i -> {'X': val, 'Y': val, 'Z': val} (single-qubit
                Pauli expectation values, averaged over all fitted pairs that
                include qubit i).
              - Tuple key (i, j) -> {('X','X'): val, ..., ('Z','Z'): val}
                (9 two-qubit correlators, no identity terms).
        """
        if pairs_list is None:
            indices = range(len(self._qubit_list))
            pairs_list = list(combinations(indices, 2))

        pairs_list = [(min(i, j), max(i, j)) for (i, j) in pairs_list]

        if output == 'expectation':
            return self._collect_expectation_values(pairs_list)

        return {p: self._fit_ij(*p, method=method, output=output) for p in pairs_list}

    def _fit_ij(self, i, j, method='lstsq', output='density_matrix'):
        assert i != j, "i and j must be different"

        # Normalise so lo < hi: the observables assume the higher-indexed qubit
        # is the first (more significant) character in the marginal bitstring.
        lo, hi = min(i, j), max(i, j)

        # Scan all circuits; keep the first occurrence of each basis combination.
        # This works for both the general coloring scheme and the bipartite scheme.
        data = {}
        for circ in self._circuits:
            tup = literal_eval(circ.name)
            key = (tup[lo], tup[hi])
            if key not in data:
                data[key] = _marginal_counts(self._result.get_counts(circ), [lo, hi])

        expected = set(product(['X', 'Y', 'Z'], ['X', 'Y', 'Z']))
        if set(data.keys()) != expected:
            raise ValueError(
                "Could not find all 9 required Pauli basis measurements for "
                f"qubit pair ({i}, {j})"
            )

        if output == 'expectation':
            return self._evaluate_expectation(data)

        if output == 'density_matrix':
            exp_vals = self._evaluate_expectation(data)
            rho = _linear_inversion(exp_vals)
            if method == 'lstsq':
                return _project_psd(rho)
            if method == 'mle':
                return _mle(data)
            raise ValueError(f"Unknown method '{method}'. Use 'lstsq' or 'mle'.")

        raise ValueError("output must be 'density_matrix' or 'expectation'")

    def _collect_expectation_values(self, pairs_list):
        """
        Compute single- and two-qubit expectation values for all pairs.

        Returns a unified dict:
          - Integer key i -> {'X': val, 'Y': val, 'Z': val}
          - Tuple key (i, j) -> {('X','X'): val, ..., ('Z','Z'): val}
        """
        single_accum = {}  # i -> {basis: [values]}
        pair_result = {}

        for (i, j) in pairs_list:
            all15 = self._fit_ij(i, j, output='expectation')
            lo, hi = min(i, j), max(i, j)

            # Two-qubit correlators (no identity)
            pair_result[(lo, hi)] = {
                (a, b): all15[(a, b)]
                for a in ('X', 'Y', 'Z')
                for b in ('X', 'Y', 'Z')
            }

            # Single-qubit marginals: (a, 'I') -> qubit lo, ('I', b) -> qubit hi
            for basis in ('X', 'Y', 'Z'):
                single_accum.setdefault(lo, {b: [] for b in ('X', 'Y', 'Z')})
                single_accum[lo][basis].append(all15[(basis, 'I')])
                single_accum.setdefault(hi, {b: [] for b in ('X', 'Y', 'Z')})
                single_accum[hi][basis].append(all15[('I', basis)])

        result = {
            i: {b: float(np.mean(vals)) for b, vals in bdict.items()}
            for i, bdict in single_accum.items()
        }
        result.update(pair_result)
        return result

    def _evaluate_expectation(self, data):
        """
        Compute all single- and two-qubit Pauli expectation values from
        marginalized counts.

        Returns:
            dict mapping (basis_i, basis_j) -> float for all 15 non-trivial
            pairs from {I, X, Y, Z} x {I, X, Y, Z}.
        """
        paulis = ['I', 'X', 'Y', 'Z']
        result = {}
        for key in product(paulis, paulis):
            if key == ('I', 'I'):
                continue
            a, b = key
            if a == 'I':
                result[key] = _average_data(data[('Z', b)], _OBSERVABLE_SECOND)
            elif b == 'I':
                result[key] = _average_data(data[(a, 'Z')], _OBSERVABLE_FIRST)
            else:
                result[key] = _average_data(data[key], _OBSERVABLE_CORRELATED)
        return result

