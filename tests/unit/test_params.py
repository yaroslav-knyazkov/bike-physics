"""Tests for bikephysics.core.params (architecture.md S5.1, task.md T2)."""

from __future__ import annotations

import math
import warnings

import pytest
from pydantic import ValidationError

from bikephysics.core.params import (
    BikeSpec,
    DrivetrainParams,
    bike_spec_from_dict,
)
from tests.unit.factories import make_minimal_bike_spec, make_minimal_bike_spec_dict


def test_minimal_spec_validates() -> None:
    spec = make_minimal_bike_spec()
    assert spec.frame.m_1 == 61.2
    assert spec.rear.r_2a == 0.335


def test_bike_spec_from_dict_round_trip() -> None:
    data = make_minimal_bike_spec_dict()
    spec = bike_spec_from_dict(data)
    assert isinstance(spec, BikeSpec)


def test_bike_spec_is_frozen() -> None:
    spec = make_minimal_bike_spec()
    with pytest.raises(ValidationError):
        spec.frame = spec.frame


def test_bike_spec_rejects_unknown_field() -> None:
    data = make_minimal_bike_spec_dict()
    data["frame"]["not_a_real_field"] = 1.0
    with pytest.raises(ValidationError):
        bike_spec_from_dict(data)


@pytest.mark.parametrize(
    "section,field_name",
    [
        ("frame", "m_1"),
        ("frame", "I_1"),
        ("frame", "r_1_pedal"),
        ("rear", "m_2a"),
        ("rear", "I_2a"),
        ("rear", "r_2a"),
        ("rear", "r_2a_bk"),
        ("front", "m_2b"),
        ("front", "I_2b"),
        ("front", "r_2b"),
        ("front", "r_2b_bk"),
        ("drivetrain", "I_3b"),
        ("drivetrain", "r_3b_cr"),
        ("drivetrain", "r_2a_cg"),
        ("aero", "A_1"),
        ("contact", "mu_s"),
        ("contact", "mu_k"),
        ("env", "g"),
    ],
)
def test_positive_fields_reject_zero_and_negative(section: str, field_name: str) -> None:
    for bad in (0.0, -1.0):
        data = make_minimal_bike_spec_dict()
        data[section][field_name] = bad
        with pytest.raises(ValidationError):
            bike_spec_from_dict(data)


def test_c_rr_rejects_out_of_range() -> None:
    for bad in (-0.001, 0.1, 0.5):
        data = make_minimal_bike_spec_dict()
        data["rear"]["c_2a_rr"] = bad
        with pytest.raises(ValidationError):
            bike_spec_from_dict(data)


def test_c_rr_accepts_zero() -> None:
    data = make_minimal_bike_spec_dict()
    data["rear"]["c_2a_rr"] = 0.0
    spec = bike_spec_from_dict(data)
    assert spec.rear.c_2a_rr == 0.0


def test_mu_k_greater_than_mu_s_rejected() -> None:
    data = make_minimal_bike_spec_dict()
    data["contact"] = {"mu_s": 0.5, "mu_k": 0.9}
    with pytest.raises(ValidationError, match="mu_k"):
        bike_spec_from_dict(data)


def test_mu_k_equal_to_mu_s_accepted() -> None:
    data = make_minimal_bike_spec_dict()
    data["contact"] = {"mu_s": 0.7, "mu_k": 0.7}
    spec = bike_spec_from_dict(data)
    assert spec.contact.mu_k == spec.contact.mu_s


def test_unequal_wheel_radii_warns_not_raises() -> None:
    data = make_minimal_bike_spec_dict()
    data["front"]["r_2b"] = 0.3
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        spec = bike_spec_from_dict(data)
    assert spec.rear.r_2a != spec.front.r_2b
    assert any("r_2a" in str(w.message) and "r_2b" in str(w.message) for w in caught)


def test_equal_wheel_radii_does_not_warn() -> None:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        make_minimal_bike_spec()
    assert len(caught) == 0


def test_M_is_sum_of_frame_and_both_wheel_masses() -> None:
    spec = make_minimal_bike_spec()
    assert spec.M == pytest.approx(spec.frame.m_1 + spec.rear.m_2a + spec.front.m_2b)
    assert spec.M == pytest.approx(65.0)


def test_m_eq_matches_boxed_formula() -> None:
    spec = make_minimal_bike_spec()
    r = spec.rear.r_2a
    expected = (
        spec.M
        + (spec.rear.I_2a + spec.front.I_2b) / r**2
        + spec.drivetrain.I_3b * spec.drivetrain.r_2a_cg**2 / (spec.drivetrain.r_3b_cr**2 * r**2)
    )
    assert spec.m_eq == pytest.approx(expected)
    assert spec.m_eq > spec.M  # rotational inertia strictly adds equivalent mass


def test_gear_ratio_matches_boxed_formula() -> None:
    spec = make_minimal_bike_spec()
    assert spec.gear_ratio == pytest.approx(spec.drivetrain.r_3b_cr / spec.drivetrain.r_2a_cg)


def test_radii_from_teeth_falls_back_to_direct_radii_when_no_teeth_given() -> None:
    dt = DrivetrainParams(I_3b=0.05, r_3b_cr=0.075, r_2a_cg=0.03)
    assert dt.radii_from_teeth() == (0.075, 0.03)


def test_radii_from_teeth_computes_from_tooth_counts() -> None:
    pitch = 0.0127
    dt = DrivetrainParams(
        I_3b=0.05, r_3b_cr=0.075, r_2a_cg=0.03, n_teeth_cr=50, n_teeth_cg=15, chain_pitch=pitch
    )
    r_cr, r_cg = dt.radii_from_teeth()
    expected_cr = pitch / (2.0 * math.sin(math.pi / 50))
    expected_cg = pitch / (2.0 * math.sin(math.pi / 15))
    assert r_cr == pytest.approx(expected_cr)
    assert r_cg == pytest.approx(expected_cg)
    # radii_from_teeth() is a pure helper; the stored fields are untouched.
    assert dt.r_3b_cr == 0.075
    assert dt.r_2a_cg == 0.03


def test_rider_params_default_to_unbounded() -> None:
    spec = make_minimal_bike_spec()
    assert spec.rider.P_max is None
    assert spec.rider.P_pedal_max is None
    assert spec.rider.cadence_max is None


def test_every_field_has_a_non_empty_description() -> None:
    """architecture.md S5.1: "Every field carries the LaTeX symbol in its
    `Field(description=...)`" -- a cheap structural guard against a field
    slipping in with no symbol documentation at all."""
    spec = make_minimal_bike_spec()
    for section_name, section_field in type(spec).model_fields.items():
        section_model_fields = getattr(section_field.annotation, "model_fields", None)
        if section_model_fields is None:
            continue
        for field_name, field_info in section_model_fields.items():
            assert field_info.description, f"{section_name}.{field_name} has no description"
