
# pylint: disable=missing-docstring
import unittest

import itertools
import numpy as np

from qiskit import QuantumRegister, QuantumCircuit, transpile
from qiskit.quantum_info import state_fidelity, partial_trace, DensityMatrix
from qiskit_aer import AerSimulator

from pairwise_tomography.pairwise_state_tomography_circuits import (
    pairwise_state_tomography_circuits,
)
from pairwise_tomography.pairwise_fitter import PairwiseStateTomographyFitter

n_list = [3, 4]
nshots = 5000

pauli = {
    'I': np.eye(2),
    'X': np.array([[0, 1], [1, 0]]),
    'Y': np.array([[0, -1j], [1j, 0]]),
    'Z': np.array([[1, 0], [0, -1]]),
}


def pauli_expectation(rho, i, j):
    # i and j are swapped because of Qiskit's bit convention
    return np.real(np.trace(np.kron(pauli[j], pauli[i]) @ rho))


def run_circuits(circuits, shots):
    backend = AerSimulator()
    job = backend.run(transpile(circuits, backend), shots=shots)
    return job.result()


class TestPairwiseStateTomography(unittest.TestCase):
    def test_pairwise_tomography(self):
        for n in n_list:
            with self.subTest(n=n):
                self.tomography_random_circuit(n)

    def tomography_random_circuit(self, n):
        q = QuantumRegister(n)
        qc = QuantumCircuit(q)

        psi = (2 * np.random.rand(2 ** n) - 1) + 1j * (2 * np.random.rand(2 ** n) - 1)
        psi /= np.linalg.norm(psi)

        qc.initialize(psi, q)
        rho = DensityMatrix(qc).data

        circ = pairwise_state_tomography_circuits(qc, q)
        result = run_circuits(circ, nshots)
        fitter = PairwiseStateTomographyFitter(result, circ, q)
        tomo_dm = fitter.fit()
        tomo_exp = fitter.fit(output='expectation')

        for (k, v) in tomo_dm.items():
            trace_qubits = list(range(n))
            trace_qubits.remove(k[0])
            trace_qubits.remove(k[1])
            rhok = partial_trace(rho, trace_qubits).data
            try:
                self.check_density_matrix(v, rhok)
            except Exception:
                print("Problem with density matrix:", k)
                raise
            try:
                self.check_pauli_expectation(tomo_exp, k, rhok)
            except Exception:
                print("Problem with expectation values:", k)
                raise

    def check_density_matrix(self, item, rho):
        fidelity = state_fidelity(item, rho)
        try:
            self.assertAlmostEqual(fidelity, 1, delta=4 / np.sqrt(nshots))
        except AssertionError:
            print("fidelity:", fidelity)
            raise

    def check_pauli_expectation(self, tomo_exp, pair, rho):
        lo, hi = min(pair), max(pair)
        for (a, b) in itertools.product(pauli.keys(), pauli.keys()):
            if a == "I" and b == "I":
                continue
            correct = pauli_expectation(rho, a, b)
            if a == 'I':
                tomo = tomo_exp[hi][b]
            elif b == 'I':
                tomo = tomo_exp[lo][a]
            else:
                tomo = tomo_exp[(lo, hi)][(a, b)]
            sigma = np.sqrt(max(1 - correct ** 2, 0) / nshots)
            try:
                self.assertAlmostEqual(tomo, correct, delta=4 * sigma + 1e-10)
            except AssertionError:
                print(a, b, "correct:", correct, "tomo:", tomo)
                raise

    def test_meas_qubit_specification(self):
        n = 4
        q = QuantumRegister(n)
        qc = QuantumCircuit(q)

        psi = (2 * np.random.rand(2 ** n) - 1) + 1j * (2 * np.random.rand(2 ** n) - 1)
        psi /= np.linalg.norm(psi)

        qc.initialize(psi, q)
        rho = DensityMatrix(qc).data

        measured_qubits = [q[0], q[2], q[3]]
        circ = pairwise_state_tomography_circuits(qc, measured_qubits)
        result = run_circuits(circ, nshots)
        fitter = PairwiseStateTomographyFitter(result, circ, measured_qubits)
        tomo_dm = fitter.fit()
        tomo_exp = fitter.fit(output='expectation')

        for (k, v) in tomo_dm.items():
            # k is an index into measured_qubits; find the circuit qubit indices
            qi = qc.find_bit(measured_qubits[k[0]]).index
            qj = qc.find_bit(measured_qubits[k[1]]).index
            trace_qubits = [idx for idx in range(n) if idx not in (qi, qj)]
            rhok = partial_trace(rho, trace_qubits).data
            try:
                self.check_density_matrix(v, rhok)
            except Exception:
                print("Problem with density matrix:", k)
                raise
            try:
                self.check_pauli_expectation(tomo_exp, k, rhok)
            except Exception:
                print("Problem with expectation values:", k)
                raise

    def test_multiple_registers(self):
        n = 4
        q = QuantumRegister(n // 2)
        p = QuantumRegister(n // 2)
        qc = QuantumCircuit(q, p)

        qc.h(q[0])
        qc.rx(np.pi / 4, q[1])
        qc.cx(q[0], p[0])
        qc.cx(q[1], p[1])

        rho = DensityMatrix(qc).data

        measured_qubits = q
        circ = pairwise_state_tomography_circuits(qc, measured_qubits)
        result = run_circuits(circ, nshots)
        fitter = PairwiseStateTomographyFitter(result, circ, measured_qubits)
        tomo_dm = fitter.fit()
        tomo_exp = fitter.fit(output='expectation')

        for (k, v) in tomo_dm.items():
            qi = qc.find_bit(measured_qubits[k[0]]).index
            qj = qc.find_bit(measured_qubits[k[1]]).index
            trace_qubits = [idx for idx in range(n) if idx not in (qi, qj)]
            rhok = partial_trace(rho, trace_qubits).data
            try:
                self.check_density_matrix(v, rhok)
            except Exception:
                print("Problem with density matrix:", k)
                raise
            try:
                self.check_pauli_expectation(tomo_exp, k, rhok)
            except Exception:
                print("Problem with expectation values:", k)
                raise


    def test_bipartite_autodetect(self):
        """pairs_list emits 9 circuits for a bipartite graph, falls back for non-bipartite."""
        n = 4
        q = QuantumRegister(n)
        qc = QuantumCircuit(q)
        psi = (2 * np.random.rand(2 ** n) - 1) + 1j * (2 * np.random.rand(2 ** n) - 1)
        psi /= np.linalg.norm(psi)
        qc.initialize(psi, q)
        rho = DensityMatrix(qc).data

        # Even/odd partition on 4 qubits is bipartite
        bipartite_pairs = [(0, 1), (0, 3), (2, 1), (2, 3)]
        circs = pairwise_state_tomography_circuits(qc, q, pairs_list=bipartite_pairs)
        self.assertEqual(len(circs), 9, "bipartite pairs should yield 9 circuits")

        # Triangle has an odd cycle -> must fall back to the full coloring scheme
        triangle_pairs = [(0, 1), (1, 2), (0, 2)]
        circs = pairwise_state_tomography_circuits(qc, q, pairs_list=triangle_pairs)
        self.assertGreater(len(circs), 9, "non-bipartite pairs should fall back")

        # Verify the full-scheme results are still correct for the triangle
        result = run_circuits(circs, nshots)
        fitter = PairwiseStateTomographyFitter(result, circs, q)
        tomo_dm = fitter.fit(pairs_list=triangle_pairs)
        for (i, j), rho_ij in tomo_dm.items():
            trace_out = [k for k in range(n) if k not in (i, j)]
            rhok = partial_trace(rho, trace_out).data
            try:
                self.check_density_matrix(rho_ij, rhok)
            except Exception:
                print(f"Problem with density matrix: ({i}, {j})")
                raise

    def test_square_lattice(self):
        """Nearest-neighbour pairs on a 3x3 square lattice using pairs_list."""
        rows, cols = 3, 3
        n = rows * cols

        nn_pairs = []
        for r in range(rows):
            for c in range(cols):
                idx = r * cols + c
                if c + 1 < cols:
                    nn_pairs.append((idx, idx + 1))
                if r + 1 < rows:
                    nn_pairs.append((idx, idx + cols))

        q = QuantumRegister(n)
        qc = QuantumCircuit(q)

        psi = (2 * np.random.rand(2 ** n) - 1) + 1j * (2 * np.random.rand(2 ** n) - 1)
        psi /= np.linalg.norm(psi)
        qc.initialize(psi, q)
        rho = DensityMatrix(qc).data

        circ = pairwise_state_tomography_circuits(qc, q)
        result = run_circuits(circ, nshots)
        fitter = PairwiseStateTomographyFitter(result, circ, q)

        tomo_dm = fitter.fit(pairs_list=nn_pairs)
        tomo_exp = fitter.fit(pairs_list=nn_pairs, output='expectation')

        self.assertEqual(set(tomo_dm.keys()), set(map(tuple, nn_pairs)))

        for (i, j), rho_ij in tomo_dm.items():
            trace_out = [k for k in range(n) if k not in (i, j)]
            rho_ref = partial_trace(rho, trace_out).data
            try:
                self.check_density_matrix(rho_ij, rho_ref)
            except Exception:
                print(f"Problem with density matrix: ({i}, {j})")
                raise
            try:
                self.check_pauli_expectation(tomo_exp, (i, j), rho_ref)
            except Exception:
                print(f"Problem with expectation values: ({i}, {j})")
                raise


    def test_bipartite_lattice(self):
        """Auto-detected bipartite generation on a 3x3 square lattice (9 circuits)."""
        rows, cols = 3, 3
        n = rows * cols

        q = QuantumRegister(n)
        qc = QuantumCircuit(q)

        psi = (2 * np.random.rand(2 ** n) - 1) + 1j * (2 * np.random.rand(2 ** n) - 1)
        psi /= np.linalg.norm(psi)
        qc.initialize(psi, q)
        rho = DensityMatrix(qc).data

        # Nearest-neighbour pairs; fitter index == register index since q is ordered
        nn_pairs = []
        for r in range(rows):
            for c in range(cols):
                idx = r * cols + c
                if c + 1 < cols:
                    nn_pairs.append((idx, idx + 1))
                if r + 1 < rows:
                    nn_pairs.append((idx, idx + cols))

        circs = pairwise_state_tomography_circuits(qc, q, pairs_list=nn_pairs)
        self.assertEqual(len(circs), 9)  # checkerboard is bipartite -> 9 circuits

        result = run_circuits(circs, nshots)
        fitter = PairwiseStateTomographyFitter(result, circs, q)

        tomo_dm = fitter.fit(pairs_list=nn_pairs)
        tomo_exp = fitter.fit(pairs_list=nn_pairs, output='expectation')

        for (i, j), rho_ij in tomo_dm.items():
            trace_out = [k for k in range(n) if k not in (i, j)]
            rho_ref = partial_trace(rho, trace_out).data
            try:
                self.check_density_matrix(rho_ij, rho_ref)
            except Exception:
                print(f"Problem with density matrix: ({i}, {j})")
                raise
            try:
                self.check_pauli_expectation(tomo_exp, (i, j), rho_ref)
            except Exception:
                print(f"Problem with expectation values: ({i}, {j})")
                raise


if __name__ == '__main__':
    unittest.main()
