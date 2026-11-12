# This file is dual licensed under the terms of the Apache License, Version
# 2.0, and the BSD License. See the LICENSE file in the root of this repository
# for complete details.

"""Check dependency grammar and semantics against independently generated values."""

from __future__ import annotations

import operator
import string
from typing import NamedTuple

import pytest
from hypothesis import given
from hypothesis import strategies as st

from packaging.markers import InvalidMarker, Marker
from packaging.requirements import InvalidRequirement, Requirement

from .strategies import SETTINGS

pytestmark = pytest.mark.property

ENVIRONMENT = {"os_name": "posix", "sys_platform": "linux"}
WHITESPACE = st.sampled_from(["", " ", "\t", " \t "])
STRING_CHARACTERS = (
    string.ascii_letters + string.digits + " \t" + string.punctuation.replace("\\", "")
)
RELEASES = st.tuples(*(st.integers(min_value=0, max_value=20) for _ in range(3)))
COMPARISONS = {
    "==": operator.eq,
    "!=": operator.ne,
    "<": operator.lt,
    "<=": operator.le,
    ">": operator.gt,
    ">=": operator.ge,
}


class Expression(NamedTuple):
    """Marker text paired with its independently computed Boolean value."""

    source: str
    expected: bool


@st.composite
def string_atoms(draw: st.DrawFn) -> Expression:
    """Produce spec-defined String comparisons without using packaging as an oracle."""
    variable = draw(st.sampled_from(list(ENVIRONMENT)))
    quote = draw(st.sampled_from(["'", '"']))
    values = st.text(alphabet=STRING_CHARACTERS.replace(quote, ""), max_size=12)
    value = draw(st.one_of(values, st.sampled_from(["posix", "linux", "3.10", ""])))
    operation = draw(st.sampled_from([*COMPARISONS, "in", "not in"]))
    literal = f"{quote}{value}{quote}"
    lhs, rhs = ENVIRONMENT[variable], value
    left, right = variable, literal
    if draw(st.booleans()):
        lhs, rhs = rhs, lhs
        left, right = right, left

    if operation == "in":
        expected = lhs in rhs
    elif operation == "not in":
        expected = lhs not in rhs
    elif operation in ("<", ">"):
        expected = False
    elif operation in ("<=", ">="):
        expected = lhs == rhs
    else:
        expected = COMPARISONS[operation](lhs, rhs)

    return Expression(f"{left} {operation} {right}", expected)


def combine(left: Expression, operation: str, right: Expression) -> Expression:
    """Keep the expected Boolean tree separate from the generated marker text."""
    if operation == "and":
        expected = left.expected and right.expected
    else:
        expected = left.expected or right.expected

    return Expression(f"({left.source}) {operation} ({right.source})", expected)


EXPRESSIONS = st.recursive(
    string_atoms(),
    lambda children: st.builds(
        combine, children, st.sampled_from(["and", "or"]), children
    ),
    max_leaves=10,
)


@given(expression=EXPRESSIONS, before=WHITESPACE, after=WHITESPACE)
@SETTINGS
def test_string_expression_matches_boolean_model(
    expression: Expression, before: str, after: str
) -> None:
    marker = Marker(before + expression.source + after)
    assert marker.evaluate(ENVIRONMENT) == expression.expected


@given(a=st.booleans(), b=st.booleans(), c=st.booleans())
@SETTINGS
def test_and_binds_more_tightly_than_or(a: bool, b: bool, c: bool) -> None:
    def atom(value: bool) -> str:
        return 'os_name == "posix"' if value else 'os_name == "nt"'

    source = f"{atom(a)} or {atom(b)} and {atom(c)}"
    assert Marker(source).evaluate(ENVIRONMENT) == (a or (b and c))
    assert Marker(f"({atom(a)} or {atom(b)}) and {atom(c)}").evaluate(ENVIRONMENT) == (
        (a or b) and c
    )


@given(
    required=RELEASES,
    candidate=RELEASES,
    operation=st.sampled_from(list(COMPARISONS)),
    whitespace=WHITESPACE,
)
@SETTINGS
def test_requirement_specifier_preserves_release_order_and_isolation(
    required: tuple[int, int, int],
    candidate: tuple[int, int, int],
    operation: str,
    whitespace: str,
) -> None:
    version = ".".join(map(str, required))
    text = f"demo[feature]{whitespace}{operation}{whitespace}{version}"
    requirement = Requirement(text)
    other = Requirement(text)

    assert requirement.name == "demo"
    assert requirement.extras == {"feature"}
    assert requirement.url is None
    assert requirement.marker is None
    assert requirement.specifier.contains(
        ".".join(map(str, candidate)), prereleases=True
    ) == COMPARISONS[operation](candidate, required)

    original = other.specifier.prereleases
    requirement.specifier.prereleases = not bool(original)
    requirement.extras.add("another")
    assert other.specifier.prereleases == original
    assert other.extras == {"feature"}

    first_specifier = next(iter(requirement.specifier))
    second_specifier = next(iter(other.specifier))
    original = second_specifier.prereleases
    first_specifier.prereleases = not bool(original)
    assert second_specifier.prereleases == original


@given(value=st.text(alphabet="ab \\é☃\n\t", max_size=12))
@SETTINGS
def test_supported_escape_literals_keep_their_value(value: str) -> None:
    # Escapes and non-ASCII literals are optional spec extensions supported here.
    encoded = value.encode("unicode_escape").decode("ascii")
    marker = Marker(f'os_name == "{encoded}"')
    assert marker.evaluate({"os_name": value})
    assert not marker.evaluate({"os_name": value + "!"})


@given(
    suffix=st.text(
        alphabet=string.ascii_letters + string.digits + "_", min_size=1, max_size=12
    ),
    variable=st.sampled_from(["os_name", "python_version", "sys_platform"]),
)
@SETTINGS
def test_variable_names_require_a_token_boundary(variable: str, suffix: str) -> None:
    source = f'{variable}{suffix} == "a"'
    with pytest.raises(InvalidMarker):
        Marker(source)
    with pytest.raises(InvalidRequirement):
        Requirement("demo; " + source)


@given(operation=st.sampled_from(["=", "<>", "=>", "!", "==!", "~~"]))
@SETTINGS
def test_non_grammar_operators_are_rejected(operation: str) -> None:
    with pytest.raises(InvalidMarker):
        Marker(f'os_name {operation} "posix"')
    with pytest.raises(InvalidRequirement):
        Requirement(f"demo{operation}1.0")
