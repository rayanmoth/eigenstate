"""
Pairwise tomography circuit generation
"""
import numpy as np
from itertools import product as _product

from qiskit import ClassicalRegister
from qiskit.circuit import QuantumRegister, Qubit


def _get_qubits(measured_qubits):
    """Return a flat list of Qubit objects from a register, qubit, or list thereof."""
    if isinstance(measured_qubits, Qubit):
        return [measured_qubits]
    if isinstance(measured_qubits, QuantumRegister):
        return list(measured_qubits)
    result = []
    for item in measured_qubits:
        if isinstance(item, Qubit):
            result.append(item)
        else:
            result.extend(list(item))
    return result


def _bipartite_coloring(pairs):
    """
    BFS 2-coloring of the graph defined by pairs (integer node indices).

    Returns a dict mapping node -> 0 or 1 if the graph is bipartite,
    or None if an odd cycle is found.
    """
    adj = {}
    for i, j in pairs:
        adj.setdefault(i, []).append(j)
        adj.setdefault(j, []).append(i)

    color = {}
    for start in adj:
        if start in color:
            continue
        color[start] = 0
        queue = [start]
        while queue:
            node = queue.pop(0)
            for neighbor in adj[node]:
                if neighbor not in color:
                    color[neighbor] = 1 - color[node]
                    queue.append(neighbor)
                elif color[neighbor] == color[node]:
                    return None
    return color


def _bipartite_circuits(circuit, meas_qubits, coloring):
    """
    Generate 9 circuits that cover all 9 Pauli basis combinations for
    any pair (i, j) where coloring[i] != coloring[j].

    Qubits absent from coloring (not in any requested pair) are assigned
    color 0 and receive the A-partition basis.
    """
    N = len(meas_qubits)
    cr = ClassicalRegister(N)
    output_circuit_list = []
    for basis_a, basis_b in _product(['X', 'Y', 'Z'], ['X', 'Y', 'Z']):
        circ = circuit.copy()
        circ.add_register(cr)
        name = []
        for bit_index, qubit in enumerate(meas_qubits):
            basis = basis_b if coloring.get(bit_index, 0) == 1 else basis_a
            if basis == 'Y':
                circ.sdg(qubit)
            if basis != 'Z':
                circ.h(qubit)
            circ.measure(qubit, cr[bit_index])
            name.append(basis)
        circ.name = str(tuple(name))
        output_circuit_list.append(circ)
    return output_circuit_list


def pairwise_state_tomography_circuits(circuit, measured_qubits, pairs_list=None):
    """
    Generate a minimal set of circuits for pairwise state tomography.

    Measurements are in the Pauli basis. When pairs_list is given and the
    pairs form a bipartite graph, exactly 9 circuits are produced (one per
    Pauli basis combination across the bipartition). Otherwise the coloring
    scheme from arXiv:1909.12814 is used: 3 uniform circuits plus
    6 * ceil(log3(N)) heterogeneous circuits.

    Args:
        circuit (QuantumCircuit): the state-preparation circuit to be
            tomographed. Must not contain a classical register.
        measured_qubits: qubits to measure, as a QuantumRegister, a list of
            Qubit objects, or a list of QuantumRegisters.
        pairs_list (list[tuple] | None): pairs of indices into measured_qubits
            for which tomography is needed. When provided, bipartite structure
            is detected automatically. Ignored for circuit generation when the
            graph is not bipartite.

    Returns:
        list[QuantumCircuit]: circuits with tomography measurements appended.
            Circuit names are string representations of per-qubit basis tuples,
            e.g. "('X', 'Y', 'Z', 'X')".
    """
    meas_qubits = _get_qubits(measured_qubits)
    N = len(meas_qubits)

    if pairs_list is not None:
        coloring = _bipartite_coloring(pairs_list)
        if coloring is not None:
            return _bipartite_circuits(circuit, meas_qubits, coloring)

    cr = ClassicalRegister(N)

    # Uniform measurement settings
    X = circuit.copy(name=str(('X',) * N))
    Y = circuit.copy(name=str(('Y',) * N))
    Z = circuit.copy(name=str(('Z',) * N))

    X.add_register(cr)
    Y.add_register(cr)
    Z.add_register(cr)

    for bit_index, qubit in enumerate(meas_qubits):
        X.h(qubit)
        Y.sdg(qubit)
        Y.h(qubit)

        X.measure(qubit, cr[bit_index])
        Y.measure(qubit, cr[bit_index])
        Z.measure(qubit, cr[bit_index])

    output_circuit_list = [X, Y, Z]

    # Heterogeneous measurement settings
    # All 6 permutations of [X, Y, Z]
    sequences = []
    meas_bases = ['X', 'Y', 'Z']
    for i in range(3):
        for j in range(2):
            meas_bases_copy = meas_bases[:]
            sequence = [meas_bases_copy[i]]
            meas_bases_copy.remove(meas_bases_copy[i])
            sequence.append(meas_bases_copy[j])
            meas_bases_copy.remove(meas_bases_copy[j])
            sequence.append(meas_bases_copy[0])
            sequences.append(sequence)

    nlayers = int(np.ceil(np.log(float(N)) / np.log(3.0)))

    for layout in range(nlayers):
        for sequence in sequences:
            meas_layout = circuit.copy()
            meas_layout.add_register(cr)
            meas_layout.name = ()
            for bit_index, qubit in enumerate(meas_qubits):
                local_basis = sequence[int(float(bit_index) / float(3 ** layout)) % 3]
                if local_basis == 'Y':
                    meas_layout.sdg(qubit)
                if local_basis != 'Z':
                    meas_layout.h(qubit)
                meas_layout.measure(qubit, cr[bit_index])
                meas_layout.name += (local_basis,)
            meas_layout.name = str(meas_layout.name)
            output_circuit_list.append(meas_layout)

    return output_circuit_list
