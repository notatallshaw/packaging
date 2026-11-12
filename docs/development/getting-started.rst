Getting started
===============

Use Python 3.14 and the shared development lock to set up an environment:

.. code-block:: console

    $ python -m venv .venv
    $ . .venv/bin/activate
    $ python -m pip install "pip>=26.3"
    $ python -m pip install -r pylock.dev.toml
    $ python -m pip install --no-deps --no-build-isolation -e .

The lock uses the standard PEP 751 format. For example, uv can install
the same development selection:

.. code-block:: console

    $ uv venv --python 3.14
    $ uv pip install -r pylock.dev.toml
    $ uv pip install --no-deps --no-build-isolation -e .

Nox selects each session's dependencies from this file, retaining its usual
environment backend. Named lock selections use pip 26.3 or newer. Select
one session group at a time; ``dev`` combines the development tools.

Running tests
~~~~~~~~~~~~~

The packaging unit tests are found in the ``tests/`` directory and are
designed to be run using `pytest`_. `pytest`_ will discover the tests
automatically, so all you have to do is:

.. code-block:: console

    $ python -m pytest
    ...
    29204 passed, 4 skipped, 1 xfailed in 83.98 seconds

This runs the tests with the default Python interpreter. This also allows
you to run select tests instead of the entire test suite.

You can also verify that the tests pass on other supported Python interpreters.
For this we use `nox`_, which will automatically create a `virtualenv`_ for
each supported Python version and run the tests. For example:

.. code-block:: console

    $ nox -s tests
    ...
    nox > Ran multiple sessions:
    nox > * tests-3.10: success
    nox > * tests-3.11: success
    nox > * tests-3.12: success
    nox > * tests-3.13: success
    nox > * tests-3.14: success
    nox > * tests-3.15: success
    nox > * tests-3.13t: success
    nox > * tests-3.14t: success
    nox > * tests-3.15t: success
    nox > * tests-pypy3.10: skipped
    nox > * tests-pypy3.11: skipped

You may not have all the required Python versions installed, in which case you
will see one or more ``InterpreterNotFound`` errors.

Running property-based tests
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The tests under ``tests/property/`` use `Hypothesis`_ and are gated by the
``property`` pytest marker. They are excluded from the default ``pytest``
run (and from ``nox -s tests``) so that downstream packagers and CI stay
fast. To run them explicitly:

.. code-block:: console

    $ python -m pytest -m property

Or via `nox`_:

.. code-block:: console

    $ nox -s property_tests

Running linters
~~~~~~~~~~~~~~~

If you wish to run the linting rules, you may use `pre-commit`_ or run
``nox -s lint``.

.. code-block:: console

    $ nox -s lint
    ...
    nox > Session lint was successful.

Building documentation
~~~~~~~~~~~~~~~~~~~~~~

packaging documentation is stored in the ``docs/`` directory. It is
written in `reStructured Text`_ and rendered using `Sphinx`_.

Use `nox`_ to build the documentation. For example:

.. code-block:: console

    $ nox -s docs
    ...
    nox > Session docs was successful.

The HTML documentation index can now be found at
``docs/_build/html/index.html``.

Updating dependencies
~~~~~~~~~~~~~~~~~~~~~

Refresh the development and build locks with:

.. code-block:: console

    $ nox -s locks -- --upgrade

``nox -s locks -- --locked`` checks the committed inputs without updating
dependencies. A weekly workflow opens or updates a draft dependency PR.

Downstream selections come from the releases in ``noxfile.py``. After changing
one of those releases, regenerate its source snapshot and dependency group
before refreshing the locks:

.. code-block:: console

    $ python -m tasks.update_downstream_groups --project build
    $ nox -s locks -- --upgrade

The snapshot records each source archive's hash and declared dependencies.
Downstream tests can also create their own test environments; those fixtures
retain their own installation rules.

Build and deployment tools use the separate
``.github/requirements/pylock.build.toml``, generated from ``build.toml`` in
the same directory for Python 3.14 on Linux. Builds use the installed backend
without creating an additional build environment.

.. _`pytest`: https://pypi.org/project/pytest/
.. _`nox`: https://pypi.org/project/nox/
.. _`virtualenv`: https://pypi.org/project/virtualenv/
.. _`pip`: https://pypi.org/project/pip/
.. _`sphinx`: https://pypi.org/project/Sphinx/
.. _`reStructured Text`: https://www.sphinx-doc.org/en/master/usage/restructuredtext/
.. _`pre-commit`: https://pre-commit.com
.. _`Hypothesis`: https://pypi.org/project/hypothesis/
