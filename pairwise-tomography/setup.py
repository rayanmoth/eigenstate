from setuptools import setup, find_packages

setup(name='pairwise-tomography',
      install_requires=['qiskit>=2.0', 'qiskit-aer', 'scipy', 'matplotlib', 'networkx'],
      version='0.1.0',
      packages=[package for package in find_packages()
                if package.startswith('pairwise_tomography')]
)
