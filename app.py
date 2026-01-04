import math
import pandas as pd
import streamlit as st

# =========================
# DN-dyn v4 demo constants
# =========================
K_HR = 20.0
K_RR = 25.0
K_SPO2 = 5.0

EPS = 0.05          # tanh shrink (keeps |T| < 1), helps avoid extreme explosions
DOMINANCE_R = 3.0   # relative dominance ratio for single-channel disturbance
MIN_D = 0.15        # minimum disturbance (1 - E) to treat as an "event" (illustrative, not clinical)

# -------------------------
# Core math (hidden % and T)
# -------------------------
def safe_pct_delta(prev: float, now: float) -> float:
    """Percent delta in %, safe against prev=0."""
    if prev == 0:
        return 0.0
    return 100.0 * (now - prev) / prev

def dyn_E(prev: float, now: float, K: float) -> float:
    """
    DN-dyn v4 internal mapping:
      raw = (%Δ)/K
      T = (1-ε)*tanh(raw)
      E = 1 - T^2
    Note: % and T are intentionally not shown in the UI.
    """
    pct = safe_pct_delta(prev, now)
    raw = pct / K
    T = (1.0 - EPS) * math.tanh(raw)
    E = 1.0 - (T * T)
    return float(E)

def fmt_arrow(prev, now):
    return f"{prev:g} → {now:g}"

# -------------------------
# Notes & labeling (v4)
# -------------------------
def channel_note(channel: str, E: float, vE: float) -> str:
    # purely descriptive labels (not clinical)
    D = 1.0 - E
    if abs(D) < 1e-9:
        return "STABLE"
    if D >= 0.60:
        return "COLLAPSE"
    if D >= 0.30:
        return "DISTURBANCE"
    if D >= 0.15:
        return "STEP_MID"
    return "MINOR"

def build_label(E_spo2, E_hr, E_rr, vE_spo2, vE_hr, vE_rr, vE_spo2_prev=None):
    """
    Returns (label, dominant_channel, pattern)
    pattern: SINGLE_CHANNEL / COHERENT / NO_EVENT / V_SHAPE
    """
    # disturbances
    D = {
        "SpO₂": max(0.0, 1.0 - E_spo2),
        "HR":   max(0.0, 1.0 - E_hr),
        "RR":   max(0.0, 1.0 - E_rr),
    }

    # sort channels by disturbance (descending)
    ranked = sorted(D.items(), key=lambda kv: kv[1], reverse=True)
    (ch1, d1), (ch2, d2), (ch3, d3) = ranked

    # NO_EVENT
    if d1 < MIN_D:
        return ("NO_EVENT", None, "NO_EVENT")

    # SINGLE_CHANNEL dominance (relative, not absolute)
    # d1 must clearly dominate d2
    if d2 <= 1e-9:
        ratio = float("inf")
    else:
        ratio = d1 / d2

    # Optional V-shape only for SpO2 across two consecutive runs (Step A then Step B)
    # If previous vE_spo2 exists (from Step A), detect a strong drop then strong recovery.
    if vE_spo2_prev is not None:
        # V-shape definition (illustrative):
        # Step A: strong negative vE on SpO2 (dominant)
        # Step B: strong positive vE on SpO2 (dominant)
        if (vE_spo2_prev <= -0.50) and (vE_spo2 >= +0.50):
            return ("SINGLE_CHANNEL_V_SHAPE (SpO₂) → ALARM_SUPPRESSED", "SpO₂", "V_SHAPE")

    if (ratio >= DOMINANCE_R) and (d1 >= MIN_D):
        # Decide "spike" vs "collapse" wording based on whether the dominant channel moved a lot
        # (we only have E/vE, so we keep it descriptive)
        if ch1 == "HR":
            return ("SINGLE_CHANNEL_SPIKE (HR) → ALARM_SUPPRESSED", "HR", "SINGLE_CHANNEL")
        if ch1 == "RR":
            return ("SINGLE_CHANNEL_COLLAPSE (RR) → ALARM_SUPPRESSED", "RR", "SINGLE_CHANNEL")
        return ("SINGLE_CHANNEL_COLLAPSE (SpO₂) → ALARM_SUPPRESSED", "SpO₂", "SINGLE_CHANNEL")

    # COHERENT shift: at least 2 channels meaningfully disturbed
    if d2 >= MIN_D:
        # direction tag (DOWN/UP) is purely indicative here; we infer from average vE sign
        avg_vE = (vE_spo2 + vE_hr + vE_rr) / 3.0
        direction = "DOWN" if avg_vE < 0 else "UP"
        return (f"COHERENT_MULTI_CHANNEL_SHIFT ({direction})", None, "COHERENT")

    # default: not clearly single-channel nor clearly coherent
    return ("INCONSISTENT_TRANSIENT (no suppression claim)", None, "INDETERMINATE")


# =========================
# UI
# =========================
st.set_page_config(page_title="DN-dyn v4 — 3-Channel Demo", layout="wide")

st.title("DN-dyn v4 — 3-Channel (HR / SpO₂ / RR) Demo")
st.caption("Illustrative demo. Outputs are descriptive only (no prediction, no diagnosis, no decision).")

# Optional: V-shape helper (two consecutive steps for SpO2)
with st.expander("Optional: SpO₂ V-shape (two-step) helper", expanded=False):
    st.write(
        "If you want to reproduce the **V-shape example**, run **Step A (Drop)** first, "
        "copy the displayed **SpO₂ vE** into the box below, then run **Step B (Recovery)**."
    )
    vE_spo2_prev_input = st.number_input(
        "Previous SpO₂ vE from Step A (Drop) (optional)", value=0.0, step=0.01, format="%.2f"
    )

st.markdown("### Inputs")

# Row 1: Prev
c1, c2, c3 = st.columns(3)
with c1:
    hr_prev = st.number_input("HR prev (bpm)", value=74.0, step=1.0)
with c2:
    spo2_prev = st.number_input("SpO₂ prev (%)", value=98.0, step=1.0)
with c3:
    rr_prev = st.number_input("RR prev (breaths/min)", value=16.0, step=1.0)

# Row 2: Now
c4, c5, c6 = st.columns(3)
with c4:
    hr_now = st.number_input("HR now (bpm)", value=75.0, step=1.0)
with c5:
    spo2_now = st.number_input("SpO₂ now (%)", value=85.0, step=1.0)
with c6:
    rr_now = st.number_input("RR now (breaths/min)", value=17.0, step=1.0)

run = st.button("Run DN-dyn v4", type="primary")

if run:
    # Compute E_dyn (hidden % and T)
    E_hr = dyn_E(hr_prev, hr_now, K_HR)
    E_spo2 = dyn_E(spo2_prev, spo2_now, K_SPO2)
    E_rr = dyn_E(rr_prev, rr_now, K_RR)

    # Two-point vE: we take previous E as 1.0 (stable reference)
    vE_hr = E_hr - 1.0
    vE_spo2 = E_spo2 - 1.0
    vE_rr = E_rr - 1.0

    # Notes per channel
    note_hr = channel_note("HR", E_hr, vE_hr)
    note_spo2 = channel_note("SpO₂", E_spo2, vE_spo2)
    note_rr = channel_note("RR", E_rr, vE_rr)

    # Build results table
    rows = [
        {"Channel": "SpO₂", "x(i-1) → x(i)": fmt_arrow(spo2_prev, spo2_now), "E_dyn": round(E_spo2, 2), "vE": round(vE_spo2, 2), "Note": note_spo2},
        {"Channel": "HR",   "x(i-1) → x(i)": fmt_arrow(hr_prev, hr_now),     "E_dyn": round(E_hr, 2),   "vE": round(vE_hr, 2),   "Note": note_hr},
        {"Channel": "RR",   "x(i-1) → x(i)": fmt_arrow(rr_prev, rr_now),     "E_dyn": round(E_rr, 2),   "vE": round(vE_rr, 2),   "Note": note_rr},
    ]
    df = pd.DataFrame(rows)

    # Label
    vE_spo2_prev = None
    if abs(vE_spo2_prev_input) > 1e-9:
        vE_spo2_prev = float(vE_spo2_prev_input)

    label, dominant, pattern = build_label(
        E_spo2=E_spo2, E_hr=E_hr, E_rr=E_rr,
        vE_spo2=vE_spo2, vE_hr=vE_hr, vE_rr=vE_rr,
        vE_spo2_prev=vE_spo2_prev
    )

    st.markdown("### Output")
    st.dataframe(df, use_container_width=True)

    st.markdown("### Label")
    st.code(label)

    st.markdown("### Parameters (shown for transparency)")
    st.write(
        {
            "K_SpO2": K_SPO2,
            "K_RR": K_RR,
            "K_HR": K_HR,
            "EPS": EPS,
            "DOMINANCE_R": DOMINANCE_R,
            "MIN_D (illustrative)": MIN_D,
        }
    )
