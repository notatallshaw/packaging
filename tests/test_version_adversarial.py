"""Native value lifetime, error propagation and cache publication contracts."""

from __future__ import annotations

import doctest
import gc
import inspect
import pickle
import subprocess
import sys
import weakref
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import _version
import pytest

from packaging.version import InvalidVersion, Version, normalize_pre, parse


class DisplayVersion(Version):
    __slots__ = ("label",)

    def __init__(self, _version: str) -> None:
        self.label = ""

    def __str__(self) -> str:
        return self.label or super().__str__()


def test_hash_preserves_component_exception() -> None:
    class BadHash(int):
        def __hash__(self) -> int:
            raise RuntimeError("component hash failed")

    value = Version.from_parts(release=(BadHash(1),))
    for _ in range(2):
        with pytest.raises(RuntimeError, match="component hash failed"):
            hash(value)


def test_failed_comparison_key_can_be_retried() -> None:
    class UnstableZero(int):
        fail = True

        def __eq__(self, other: object) -> bool:
            if self.fail:
                raise RuntimeError("component comparison failed")
            return super().__eq__(other)

        __hash__ = int.__hash__

    component = UnstableZero(0)
    value = Version.from_parts(release=(1, component))
    with pytest.raises(RuntimeError, match="component comparison failed"):
        hash(value)

    component.fail = False
    assert value == Version("1")
    assert hash(value) == hash(Version("1"))


def test_factory_callbacks_cannot_observe_partial_values() -> None:
    code = """
import gc
import sys
if sys.platform != 'win32':
    import resource
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
import _version
from packaging.version import Version
class Probe(int):
    active = False
    def __eq__(self, other):
        if not Probe.active:
            Probe.active = True
            try:
                for value in gc.get_objects():
                    if type(value) is Version:
                        hash(value)
                        repr(value)
                        value.epoch
            finally:
                Probe.active = False
        return int.__eq__(self, other)
    __hash__ = int.__hash__
value = Version.from_parts(release=(1, Probe(0)))
assert hash(value) == hash(Version('1'))
print(_version.__file__)
"""
    child = subprocess.run(
        [sys.executable, "-X", "faulthandler", "-c", code],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert child.returncode == 0, child.stderr
    assert child.stdout.strip() == _version.__file__


def test_concurrent_first_hash_publishes_one_result() -> None:
    barrier = Barrier(2)

    class BlockingHash(int):
        def __hash__(self) -> int:
            barrier.wait(timeout=10)
            return super().__hash__()

    value = Version.from_parts(release=(BlockingHash(1),))
    expected = hash(Version("1"))
    with ThreadPoolExecutor(max_workers=2) as executor:
        calls = [executor.submit(hash, value) for _ in range(2)]
        assert [call.result(timeout=15) for call in calls] == [expected, expected]
    assert hash(value) == expected


def test_full_component_cycle_is_collectible() -> None:
    class CyclicVersion(Version):
        __slots__ = ("__weakref__",)

    class Component(int):
        pass

    component = Component(65536)
    value = CyclicVersion.from_parts(release=(component,))
    component.owner = value
    reference = weakref.ref(value)
    del value, component
    gc.collect()
    assert reference() is None


def test_compact_type_cycle_is_collectible() -> None:
    def cycle() -> weakref.ReferenceType[type[Version]]:
        """Leave only a class-to-value-to-class cycle alive."""

        class TemporaryVersion(Version):
            __slots__ = ()

        TemporaryVersion.value = TemporaryVersion("1.2.3")
        return weakref.ref(TemporaryVersion)

    reference = cycle()
    gc.collect()
    assert reference() is None


def test_full_size_includes_separate_storage() -> None:
    assert sys.getsizeof(Version("65536.1")) > sys.getsizeof(Version("1.2.3"))


def test_subclass_pickle_preserves_state_and_core_value() -> None:
    original = DisplayVersion("1.2.3")
    original.label = "display text"
    restored = pickle.loads(pickle.dumps(original))
    assert type(restored) is DisplayVersion
    assert restored.label == "display text"
    assert restored.release == (1, 2, 3)
    assert restored == original


def test_foreign_private_keys_do_not_define_comparison() -> None:
    class Foreign:
        @property
        def _key(self) -> object:
            raise AssertionError("foreign private key accessed")

    value = Version("1")
    other = Foreign()
    assert value.__eq__(other) is NotImplemented
    assert value != other


def test_native_factory_signatures() -> None:
    create = inspect.signature(Version.from_parts)
    change = inspect.signature(Version("1").__replace__)
    assert create.parameters["release"].default is inspect.Parameter.empty
    assert create.parameters["release"].kind is inspect.Parameter.KEYWORD_ONLY
    assert set(create.parameters) == set(change.parameters)


@pytest.mark.parametrize(
    "value", ["\u0661.\u0662.\u0663", "1+\u017f", "1+\u0130", "1+\u212a", "1\ud800"]
)
def test_ascii_component_grammar(value: str) -> None:
    with pytest.raises(InvalidVersion):
        Version(value)


def test_base_version_does_not_retain_epoch_strings() -> None:
    code = """
import gc
import sys
import tracemalloc
import _version
from packaging.version import Version
value = Version.from_parts(epoch=10**40, release=tuple(range(500)))
expected = str(value)
assert value.base_version == expected
gc.collect()
tracemalloc.start()
before = tracemalloc.get_traced_memory()[0]
for _ in range(1000):
    assert value.base_version == expected
gc.collect()
retained = tracemalloc.get_traced_memory()[0] - before
tracemalloc.stop()
# At most two complete results cover temporary references at the boundary.
assert retained < 2 * sys.getsizeof(expected), retained
print(_version.__file__)
"""
    child = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert child.returncode == 0, child.stderr
    assert child.stdout.strip() == _version.__file__


def test_public_version_examples() -> None:
    failures, _ = doctest.testfile(
        "version_examples.txt",
        globs={"Version": Version, "normalize_pre": normalize_pre, "parse": parse},
        optionflags=doctest.ELLIPSIS,
    )
    assert failures == 0


def test_tuple_subclass_hooks_do_not_define_release_storage() -> None:
    class Release(tuple):
        __slots__ = ()

        def __getitem__(self, _index: object) -> object:
            raise AssertionError("release storage invoked user slicing")

    value = Version.from_parts(release=Release((1, 0)))
    assert type(value.release) is tuple
    assert value.release == (1, 0)
    assert value.major == 1
    assert value == Version("1")
    assert hash(value) == hash(Version("1"))


@pytest.mark.parametrize(
    "space", [chr(i) for i in range(sys.maxunicode + 1) if chr(i).isspace()]
)
def test_all_unicode_surrounding_whitespace(space: str) -> None:
    assert str(Version(space + "1.2.3" + space)) == "1.2.3"


def test_big_integer_callbacks_do_not_observe_partial_tuples() -> None:
    code = """
import gc
import sys
if sys.platform != 'win32':
    import resource
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
import _version
from packaging.version import Version
# Explicitly permit the input that enters CPython's _pylong conversion path.
sys.set_int_max_str_digits(0)
def audit(event, args):
    if event == 'import' and args[0] == '_pylong':
        for value in gc.get_objects():
            if type(value) is tuple:
                try:
                    hash(value)
                except Exception:
                    pass
sys.addaudithook(audit)
value = Version('9' * 7000 + '.0')
assert value.release[0] == int('9' * 7000)
print(_version.__file__)
"""
    child = subprocess.run(
        [sys.executable, "-X", "faulthandler", "-c", code],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert child.returncode == 0, child.stderr
    assert child.stdout.strip() == _version.__file__
