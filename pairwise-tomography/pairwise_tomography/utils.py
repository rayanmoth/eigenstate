"""
Utility functions for two-qubit entanglement and correlation measures.
"""

import numpy as np
import scipy.linalg as la
from scipy.optimize import minimize

from qiskit.quantum_info import partial_trace, entropy, DensityMatrix


def _outer(state):
    """Density matrix for a pure state vector: |psi><psi|."""
    state = np.asarray(state)
    return np.outer(state, state.conj())


def _mutual_information(rho):
    """
    Quantum mutual information I(A:B) = S(A) + S(B) - S(AB) in nats.

    rho is a 4x4 two-qubit density matrix with qubit j as the more significant
    (left) qubit and qubit i as the less significant (right) qubit.
    """
    dm = DensityMatrix(np.asarray(rho))
    s_ab = entropy(dm, base=2)
    s_j = entropy(partial_trace(dm, [0]), base=2)  # trace out qubit i
    s_i = entropy(partial_trace(dm, [1]), base=2)  # trace out qubit j
    return (s_i + s_j - s_ab) * np.log(2)          # convert bits -> nats


def concurrence(state):
    """
    Calculate the concurrence of a two-qubit state.

    Args:
        state (array): a pure state vector (length 4) or density matrix (4x4).

    Returns:
        float: concurrence in [0, 1].

    Raises:
        Exception: if the state does not describe exactly two qubits.
    """
    rho = np.array(state)
    if rho.ndim == 1:
        if len(rho) != 4:
            raise Exception("Concurrence is only defined for two-qubit states")
        rho = _outer(rho)
    elif rho.shape != (4, 4):
        raise Exception("Concurrence is only defined for two-qubit states")

    YY = np.fliplr(np.diag([-1, 1, 1, -1]))
    A = rho.dot(YY).dot(rho.conj()).dot(YY)
    w = la.eigvals(A)
    w = np.sort(np.real(w))
    w = np.sqrt(np.maximum(w, 0))
    return max(0.0, w[-1] - np.sum(w[:-1]))


_s0 = np.array([[1, 0], [0, 1]], dtype=complex)
_sx = np.array([[0, 1], [1, 0]], dtype=complex)
_sy = np.array([[0, -1j], [1j, 0]], dtype=complex)
_sz = np.array([[1, 0], [0, -1]], dtype=complex)
_paulis = np.array([_s0, _sx, _sy, _sz])


def n_vector(theta, phi):
    """Unit vector in the direction (theta, phi)."""
    return np.array([
        np.sin(theta) * np.cos(phi),
        np.sin(theta) * np.sin(phi),
        np.cos(theta),
    ])


def projective_measurement(theta, phi, qubit=0):
    """
    Two-qubit projective measurement operators along the direction (theta, phi)
    on one of the two qubits.

    Args:
        theta (float): polar angle.
        phi (float): azimuthal angle.
        qubit (int): 0 or 1 — which qubit is measured.

    Returns:
        list[np.ndarray]: [Pi_plus, Pi_minus] as 4x4 matrices.
    """
    n = n_vector(theta, phi)
    Pi1 = 0.5 * _s0 + 0.5 * sum(n[k] * _paulis[k + 1] for k in range(3))
    Pi2 = 0.5 * _s0 - 0.5 * sum(n[k] * _paulis[k + 1] for k in range(3))

    if qubit == 0:
        Pi1 = np.kron(_s0, Pi1)
        Pi2 = np.kron(_s0, Pi2)
    else:
        Pi1 = np.kron(Pi1, _s0)
        Pi2 = np.kron(Pi2, _s0)

    return [Pi1, Pi2]


def quantum_conditional_entropy(rho, theta, phi, qubit=0):
    """
    Quantum conditional entropy S(B|A) for a two-qubit state under a
    projective measurement in the direction (theta, phi) on one qubit.

    Evaluates sum_k p_k S(rho_k^B) where p_k is the outcome probability,
    rho_k^B is the post-measurement reduced state of the other qubit, and
    S is the von Neumann entropy (base 2).

    Args:
        rho (array): 4x4 two-qubit density matrix.
        theta (float): polar angle.
        phi (float): azimuthal angle.
        qubit (int): 0 or 1 — which qubit is measured.

    Returns:
        float: quantum conditional entropy (bits).
    """
    measurement = projective_measurement(theta, phi, qubit=qubit)
    prob = np.array([np.real(np.trace(p @ rho)) for p in measurement])
    rho_cond = [
        partial_trace(DensityMatrix(p @ rho @ p), [qubit]).data / prob[k]
        for k, p in enumerate(measurement)
    ]
    s_ent = np.array([entropy(DensityMatrix(r), base=2) for r in rho_cond])
    return np.sum(prob * s_ent)


def classical_correlation(rho, qubit=0):
    """
    Classical correlations J(B|A) between two qubits, maximized over all
    projective measurements on qubit A.

    Defined as in Eq. (8) of Phys. Rev. A 83, 052108 (2011), base-2 entropy.

    Args:
        rho (array): 4x4 two-qubit density matrix.
        qubit (int): 0 or 1 — which qubit is measured.

    Returns:
        float: classical correlations (bits).
    """
    assert np.asarray(rho).shape == (4, 4), "Not a two-qubit density matrix"
    cc = lambda x: quantum_conditional_entropy(rho, x[0], x[1], qubit=qubit)
    f = minimize(cc, [np.pi / 2, np.pi])
    s_b = entropy(
        partial_trace(DensityMatrix(np.asarray(rho)), [qubit]), base=2
    )
    return s_b - f.fun


def discord(rho, qubit=0):
    """
    Quantum discord D(B|A) between two qubits.

    Defined in Phys. Rev. Lett. 88, 017901 (2001). Returns a value in [0, 1]
    (bits).

    Args:
        rho (array): 4x4 two-qubit density matrix.
        qubit (int): 0 or 1 — which qubit is measured.

    Returns:
        float: quantum discord (bits).
    """
    mi_bits = _mutual_information(rho) / np.log(2)
    return mi_bits - classical_correlation(rho, qubit=qubit)
