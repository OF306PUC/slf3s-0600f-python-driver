#%%
import numpy as np
import matplotlib.pyplot as plt
from scipy.signal import freqz

# ── Parameters ────────────────────────────────────────────────
fs    = 2000        # Hz  (sensor internal sampling rate)
N     = 200         # moving average length
alpha = 0.0125      # exponential smoother coefficient
NFFT  = 16384       # frequency resolution

# ── Moving average (FIR) ──────────────────────────────────────
# H(z) = (1/N)(1 + z^-1 + ... + z^-(N-1))
b_ma = np.ones(N) / N
a_ma = 1.0
w, H_ma = freqz(b_ma, a_ma, worN=NFFT, fs=fs)
mag_ma  = 20 * np.log10(np.abs(H_ma) + 1e-12)

# ── Exponential smoother (IIR) ────────────────────────────────
# H(z) = alpha / (1 - (1-alpha)·z^-1)
b_ema = [alpha]
a_ema = [1.0, -(1 - alpha)]
_, H_ema = freqz(b_ema, a_ema, worN=NFFT, fs=fs)
mag_ema  = 20 * np.log10(np.abs(H_ema) + 1e-12)

# ── -3 dB cutoffs ─────────────────────────────────────────────
def cutoff(w, mag):
    idx = np.where(mag <= -3)[0]
    return w[idx[0]] if len(idx) else np.nan

f_ma  = cutoff(w, mag_ma)
f_ema = cutoff(w, mag_ema)

print(f"Moving average  N={N}:     f_-3dB = {f_ma:.4f} Hz")
print(f"Exponential IIR α={alpha}: f_-3dB = {f_ema:.4f} Hz")

# ── Closed-form IIR cutoff (verification) ────────────────────
beta = 1 - alpha
arg  = (2*beta**2 + 2*beta - 1) / (2*beta)
if abs(arg) <= 1:
    f_ema_exact = fs / (2*np.pi) * np.arccos(arg)
    print(f"IIR cutoff (closed-form): {f_ema_exact:.4f} Hz")

# ── Infusion dynamics reference band ─────────────────────────
# 5 mL/hr pump: dominant variation timescale ~ hours
# Anything below ~0.01 Hz is "infusion signal"
f_signal_max = 1 / 3600   # 1 cycle per hour (just for illustration)

# ── Plot ──────────────────────────────────────────────────────
fig, axes = plt.subplots(2, 1, figsize=(9, 6), sharex=True)
fig.suptitle("Respuesta en frecuencia: promedio móvil vs. suavizado exponencial\n"
             f"$f_s = {fs}$ Hz,  $N = {N}$,  $\\alpha = {alpha}$",
             fontsize=12)

colors = {"ma": "#185FA5", "ema": "#1D9E75", "ref": "#BA7517", "m3": "#D85A30"}

# ── Magnitude ─────────────────────────────────────────────────
ax = axes[0]
ax.plot(w, mag_ma,  color=colors["ma"],  lw=1.5, label=f"Promedio móvil  (N={N})")
ax.plot(w, mag_ema, color=colors["ema"], lw=1.5, label=f"Suavizado exp.  (α={alpha})")
ax.axhline(-3, color=colors["m3"], lw=0.8, ls="--", label="−3 dB")

# Cutoff annotations
for f_c, col, label in [(f_ma, colors["ma"], f"{f_ma:.2f} Hz"),
                         (f_ema, colors["ema"], f"{f_ema:.4f} Hz")]:
    ax.axvline(f_c, color=col, lw=0.8, ls=":")
    ax.annotate(label, xy=(f_c, -3),
                xytext=(f_c * 3, -22 if col == colors["ma"] else -38),
                fontsize=8, color=col,
                arrowprops=dict(arrowstyle="->", color=col, lw=0.7))

ax.set_ylabel("Magnitud (dB)")
ax.set_ylim(-90, 5)
ax.legend(fontsize=9, loc="upper right")
ax.grid(True, alpha=0.3)

# ── Phase ─────────────────────────────────────────────────────
ax = axes[1]
ax.plot(w, np.angle(H_ma,  deg=True), color=colors["ma"],  lw=1.5)
ax.plot(w, np.angle(H_ema, deg=True), color=colors["ema"], lw=1.5)
ax.set_ylabel("Fase (°)")
ax.set_xlabel("Frecuencia (Hz)")
ax.grid(True, alpha=0.3)

# Log scale on x to see both cutoffs clearly
for ax in axes:
    ax.set_xscale("log")
    ax.set_xlim(1e-2, fs / 2)

plt.tight_layout()
plt.show()