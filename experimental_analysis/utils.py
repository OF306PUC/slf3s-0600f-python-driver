"""
Post-processing ops for SLF3S-0600F sensor data: 
    - Filtering
    - Spectrogram computation
    - RK4 Integration of flow rate to compute volume
"""

import numpy as np 


NFFT = 1024

def compute_spectrogram(signal, fs, total_samples, time_per_frame, stride_time, nfft=NFFT):
    nfft = 1024
    frame_length  = int(round(time_per_frame * fs))
    stride_length = int(round(stride_time * fs))

    num_frames = 1 + (total_samples - frame_length) // stride_length
    pad_length = num_frames * stride_length + frame_length
    z = np.zeros(pad_length - total_samples)

    padded_signal = np.concatenate((signal, z))

    indices = (
        np.tile(np.arange(frame_length), (num_frames, 1)) +
        np.tile(
            np.arange(0, num_frames * stride_length, stride_length),
            (frame_length, 1)
        ).T
    )

    frames_signal = padded_signal[indices]
    window = np.hamming(frame_length)
    frames_signal_win = frames_signal * window
    mag_fft_signal = np.abs(np.fft.rfft(frames_signal_win, n=nfft))
    mag_fft_signal = mag_fft_signal.T
    return mag_fft_signal

def compute_rms(signal):
    """
    Compute RMS of the signal.
    """
    signal = np.asarray(signal)
    return np.sqrt(np.mean(signal**2))

def moving_avg_nonzero(signal, window_size):
    """
    Moving average ignoring zero values.
    Zeros remain zero in the output.
    """
    x = np.zeros_like(signal, dtype=float)
    half = window_size // 2

    for k in range(len(signal)):
        if signal[k] != 0:
            start = max(0, k - half)
            end   = min(len(signal), k + half + 1)

            window = signal[start:end]
            nz = window[window != 0]

            if nz.size > 0:
                x[k] = nz.mean()

    return x


def rk4_step(f, t, y, dt, *args):
    """
    One step of fixed-step RK4 integration.

    f : function(t, y, *args) -> dydt
    t : current time
    y : current state vector
    dt: time step
    *args: extra arguments passed to f
    """
    k1 = f(t, y, *args)
    k2 = f(t + dt/2, y + dt/2 * k1, *args)
    k3 = f(t + dt/2, y + dt/2 * k2, *args)
    k4 = f(t + dt,   y + dt   * k3, *args)
    return y + (dt/6) * (k1 + 2*k2 + 2*k3 + k4)

def f(t, y, u): 
    V_dot = u 
    return V_dot

def integrate_flow_rate(time, flow_rate_func): 
    volumen = np.zeros_like(time)
    for k in range(1, len(time)):
        dt = time[k] - time[k-1]
        u = flow_rate_func[k-1]
        volumen[k] = rk4_step(f, time[k-1], volumen[k-1], dt, u)

    return volumen




