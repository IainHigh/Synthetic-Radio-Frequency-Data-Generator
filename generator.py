#!/usr/bin/python3

## python imports
import argparse
import sys
import json
import numpy as np
import os
import ctypes
from tqdm import tqdm
from utils import *

buf = 4096
halfbuf = 2048

rng_seed = None

# Read the configs/system_parameters.json file.
with open("./configs/system_parameters.json") as f:
    system_parameters = json.load(f)

working_directory = system_parameters["Working_Directory"]
sys.path.append(working_directory)

dataset_directory = system_parameters["Dataset_Directory"]

## load c modules
clinear = ctypes.CDLL(os.path.abspath("./cmodules/linear_modulate"))
cam = ctypes.CDLL(os.path.abspath("./cmodules/am_modulate"))
cfm = ctypes.CDLL(os.path.abspath("./cmodules/fm_modulate"))
cfsk = ctypes.CDLL(os.path.abspath("./cmodules/fsk_modulate"))
ctx = ctypes.CDLL(os.path.abspath("./cmodules/rrc_tx"))
cchan = ctypes.CDLL(os.path.abspath("./cmodules/channel"))

import numpy as np


def calculate_ber_BPSK(xI, xQ, yI, yQ, sps, trim):
    """
    Calculate the Bit Error Rate (BER) for BPSK.

    Parameters
    ----------
    xI, xQ : array_like
        Transmitted (baseband) in-phase and quadrature components.
    yI, yQ : array_like
        Received in-phase and quadrature components (after channel, frequency shift, etc.).
    sps : int
        Samples per symbol.
    trim : int
        Number of samples to trim from beginning and end.

    Returns
    -------
    ber : float
        Bit error rate.
    """
    # Convert inputs to numpy arrays
    xI = np.array(xI)
    xQ = np.array(xQ)
    yI = np.array(yI)
    yQ = np.array(yQ)

    # Trim the signals (to match what is saved/used)
    tx_I = xI[trim:-trim]
    tx_Q = xQ[trim:-trim]
    rx_I = yI[trim:-trim]
    rx_Q = yQ[trim:-trim]

    rx_complex = rx_I + 1j * rx_Q
    tx_complex = tx_I + 1j * tx_Q

    tx_symbols = tx_complex[::sps]
    rx_symbols = rx_complex[::sps]

    # Demap the symbols to bits (BPSK decision on the real part).
    tx_bits = (np.real(tx_symbols) >= 0).astype(int)
    rx_bits = (np.real(rx_symbols) >= 0).astype(int)

    # Demap the symbols to bits (BPSK decision on the real part).
    tx_bits = (np.real(tx_symbols) >= 0).astype(int)
    rx_bits = (np.real(rx_symbols) >= 0).astype(int)

    # # Calculate bit errors and BER.
    bit_errors = np.sum(tx_bits != rx_bits)
    total_bits = len(tx_bits)
    ber = bit_errors / total_bits

    return ber


def generate_linear(idx_start, mod, config):
    verbose = ctypes.c_int(config["verbose"])
    modtype = ctypes.c_int(mod[0])
    n_samps = config["n_samps"] + buf
    sampling_rate = config["sampling_rate"]

    # Generate time vector for mixing
    t = np.arange(n_samps - buf) / sampling_rate

    sig_params = [
        (_sps, _beta, _delay, _dt)
        for _sps in config["symbol_rate"]
        for _beta in config["rrc_filter"]["beta"]
        for _delay in config["rrc_filter"]["delay"]
        for _dt in config["rrc_filter"]["dt"]
    ]
    idx = np.random.choice(len(sig_params), config["n_captures"])
    sig_params = [sig_params[_idx] for _idx in idx]
    idx = np.random.choice(len(config["channel_params"]), config["n_captures"])
    channel_params = [config["channel_params"][_idx] for _idx in idx]
    channel_type = config["channel_type"]

    center_frequencies = config["center_frequencies"]

    ber_dict = {}

    for i in tqdm(
        range(0, config["n_captures"]), desc=f"Generating Data for: {mod[-1]}"
    ):
        seed = ctypes.c_int(rng_seed)

        I_total = np.zeros(n_samps - buf, dtype=np.float32)
        Q_total = np.zeros(n_samps - buf, dtype=np.float32)

        for center_freq in center_frequencies:

            # Extract channel parameters based on type
            if channel_type == "awgn":
                snr, fo, po = channel_params[i]
                snr = ctypes.c_float(snr)
                fo = ctypes.c_float(fo)
                po = ctypes.c_float(po)

            elif channel_type == "rayleigh":
                (
                    snr,
                    fo,
                    po,
                    awgn_flag,
                    path_delays,
                    path_gains,
                ) = channel_params[i]

                assert len(path_delays) == len(
                    path_gains
                ), "Path delays and path gains must have the same length."
                snr = ctypes.c_float(snr)
                fo = ctypes.c_float(fo)
                po = ctypes.c_float(po)
                num_taps = ctypes.c_int(len(path_delays))
                awgn = ctypes.c_int(awgn_flag)

                # Convert path_delays and path_gains to ctypes arrays
                path_delays_ctypes = (ctypes.c_float * len(path_delays))(*path_delays)
                path_gains_ctypes = (ctypes.c_float * len(path_gains))(*path_gains)

            elif channel_type == "rician":
                (
                    snr,
                    fo,
                    po,
                    k_factor,
                    awgn_flag,
                    path_delays,
                    path_gains,
                ) = channel_params[i]
                assert len(path_delays) == len(
                    path_gains
                ), "Path delays and path gains must have the same length."
                snr = ctypes.c_float(snr)
                fo = ctypes.c_float(fo)
                po = ctypes.c_float(po)
                k_factor = ctypes.c_float(k_factor)
                num_taps = ctypes.c_int(len(path_delays))
                awgn = ctypes.c_int(awgn_flag)

                # Convert path_delays and path_gains to ctypes arrays
                path_delays_ctypes = (ctypes.c_float * len(path_delays))(*path_delays)
                path_gains_ctypes = (ctypes.c_float * len(path_gains))(*path_gains)

            else:
                raise ValueError("Undefined channel type.")

            order = ctypes.c_int(mod[1])
            sps = ctypes.c_int(sig_params[i][0])
            beta = ctypes.c_float(sig_params[i][1])
            delay = ctypes.c_uint(int(sig_params[i][2]))
            dt = ctypes.c_float(sig_params[i][3])

            # Adjust n_sym for chunk processing
            n_sym = int(
                np.ceil(n_samps / sps.value)
            )  # Ensure the right number of symbols

            # Create return arrays
            s = (ctypes.c_uint * n_sym)(*np.zeros(n_sym, dtype=int))
            smI = (ctypes.c_float * n_sym)(*np.zeros(n_sym))
            smQ = (ctypes.c_float * n_sym)(*np.zeros(n_sym))
            xI = (ctypes.c_float * n_samps)(*np.zeros(n_samps))
            xQ = (ctypes.c_float * n_samps)(*np.zeros(n_samps))
            yI = (ctypes.c_float * n_samps)(*np.zeros(n_samps))
            yQ = (ctypes.c_float * n_samps)(*np.zeros(n_samps))

            # Call C modules for chunk processing
            clinear.linear_modulate(
                modtype, order, ctypes.c_int(n_sym), s, smI, smQ, verbose, seed
            )
            ctx.rrc_tx(
                ctypes.c_int(n_sym), sps, delay, beta, dt, smI, smQ, xI, xQ, verbose
            )

            # Channel Type
            if channel_type == "awgn":
                cchan.channel(snr, n_sym, sps, fo, po, xI, xQ, yI, yQ, verbose, seed)
            elif channel_type == "rayleigh":
                cchan.rayleigh_channel(
                    snr,
                    n_sym,
                    sps,
                    fo,
                    po,
                    num_taps,
                    awgn,
                    xI,
                    xQ,
                    yI,
                    yQ,
                    path_delays_ctypes,
                    path_gains_ctypes,
                    verbose,
                    seed,
                )
            elif channel_type == "rician":
                cchan.rician_channel(
                    snr,
                    n_sym,
                    sps,
                    fo,
                    po,
                    k_factor,
                    num_taps,
                    awgn,
                    xI,
                    xQ,
                    yI,
                    yQ,
                    path_delays_ctypes,
                    path_gains_ctypes,
                    verbose,
                    seed,
                )

            I = np.array(yI)[halfbuf:-halfbuf]
            Q = np.array(yQ)[halfbuf:-halfbuf]

            # Apply frequency shift for wideband signal
            freq_shift = np.exp(1j * 2 * np.pi * center_freq * t)

            I_shifted = I * np.real(freq_shift) - Q * np.imag(freq_shift)
            Q_shifted = I * np.imag(freq_shift) + Q * np.real(freq_shift)

            # Sum to create the wideband signal
            I_total += I_shifted
            Q_total += Q_shifted

        # Normalize final signal
        # max_amp = max(np.max(np.abs(I_total)), np.max(np.abs(Q_total)))
        # if max_amp > 0:
        #    I_total /= max_amp
        #    Q_total /= max_amp

        # Metadata
        metadata = {
            "modname": mod[-1],
            "modclass": modtype.value,
            "order": order.value,
            "n_samps": n_samps - buf,
            "sampling_rate": config["sampling_rate"],
            "center_frequencies": center_frequencies,
            "channel_type": config["channel_type"],
            "snr": snr.value,
            "filter_type": "rrc",
            "sps": sps.value,
            "fo": fo.value,
            "po": po.value,
            "delay": delay.value,
            "beta": beta.value,
            "dt": dt.value,
            "savepath": config["savepath"],
            "savename": config["savename"],
        }
        if mod[-1] == "bpsk":
            ber = calculate_ber_BPSK(xI, xQ, yI, yQ, sps.value, trim=halfbuf)
            if snr.value not in ber_dict:
                ber_dict[snr.value] = [ber]
            else:
                ber_dict[snr.value].append(ber)

        # Save the concatenated data for this capture in SigMF format
        save_sigmf(I_total, Q_total, metadata, idx_start + i)

    # After processing all captures, calculate and print the average BER per SNR.
    avg_ber_dict = {
        snr: sum(ber_list) / len(ber_list) for snr, ber_list in ber_dict.items()
    }

    # Sort the dictionary by SNR
    avg_ber_dict = dict(sorted(avg_ber_dict.items()))

    if avg_ber_dict != {}:
        print("Average BER per SNR:")
        for snr, avg_ber in avg_ber_dict.items():
            print(f"SNR = {snr}: AVG_BER = {avg_ber}")

    return idx_start + config["n_captures"]


def generate_am(idx_start, mod, config):
    verbose = ctypes.c_int(config["verbose"])
    modtype = ctypes.c_int(mod[0])
    n_samps = ctypes.c_int(config["n_samps"] + buf)

    sig_params = config["am_defaults"]["modulation_index"]
    idx = np.random.choice(len(sig_params), config["n_captures"])
    sig_params = [sig_params[_idx] for _idx in idx]
    idx = np.random.choice(len(config["channel_params"]), config["n_captures"])
    channel_params = [config["channel_params"][_idx] for _idx in idx]

    for i in range(0, config["n_captures"]):
        seed = ctypes.c_int(rng_seed)
        snr = ctypes.c_float(channel_params[i][0])
        fo = ctypes.c_float(2.0 * channel_params[i][1] * np.pi)
        po = ctypes.c_float(channel_params[i][2])

        modtype = ctypes.c_int(mod[1])
        mod_idx = ctypes.c_float(sig_params[i])
        sps = ctypes.c_int(1)

        ## create return arrays
        x = (ctypes.c_float * n_samps.value)(*np.zeros(n_samps.value))
        xI = (ctypes.c_float * n_samps.value)(*np.zeros(n_samps.value))
        xQ = (ctypes.c_float * n_samps.value)(*np.zeros(n_samps.value))
        yI = (ctypes.c_float * n_samps.value)(*np.zeros(n_samps.value))
        yQ = (ctypes.c_float * n_samps.value)(*np.zeros(n_samps.value))

        ## calls to c code
        cam.am_modulate(modtype, mod_idx, n_samps, x, xI, xQ, verbose, seed)
        cchan.channel(snr, n_samps, sps, fo, po, xI, xQ, yI, yQ, verbose, seed)

        metadata = {
            "modname": mod[-1],
            "modclass": mod[0],
            "modvariant": mod[1],
            "mod_idx": mod_idx.value,
            "n_samps": n_samps.value - buf,
            "channel_type": config["channel_type"],
            "snr": snr.value,
            "fo": fo.value,
            "po": po.value,
            "savepath": config["savepath"],
            "savename": config["savename"],
        }

        ## convert to numpy arrays
        I = np.array([_i for _i in yI])
        I = I[halfbuf:-halfbuf]
        Q = np.array([_q for _q in yQ])
        Q = Q[halfbuf:-halfbuf]

        ## save record in sigmf format
        save_sigmf(I, Q, metadata, idx_start + i)

    return idx_start + i + 1


def generate_fm(idx_start, mod, config):
    verbose = ctypes.c_int(config["verbose"])
    modtype = ctypes.c_int(mod[0])
    n_samps = ctypes.c_int(config["n_samps"] + buf)

    if mod[1] == 0:
        ## narrowband
        sig_params = config["fmnb_defaults"]["modulation_factor"]
    elif mod[1] == 1:
        ## wideband
        sig_params = config["fmwb_defaults"]["modulation_factor"]
    idx = np.random.choice(len(sig_params), config["n_captures"])
    sig_params = [sig_params[_idx] for _idx in idx]
    idx = np.random.choice(len(config["channel_params"]), config["n_captures"])
    channel_params = [config["channel_params"][_idx] for _idx in idx]

    for i in range(0, config["n_captures"]):
        seed = ctypes.c_int(rng_seed)

        mod_factor = ctypes.c_float(sig_params[i])

        snr = ctypes.c_float(channel_params[i][0])
        fo = ctypes.c_float(2.0 * channel_params[i][1] * np.pi)
        po = ctypes.c_float(channel_params[i][2])

        sps = ctypes.c_int(1)

        ## create return arrays
        x = (ctypes.c_float * n_samps.value)(*np.zeros(n_samps.value))
        xI = (ctypes.c_float * n_samps.value)(*np.zeros(n_samps.value))
        xQ = (ctypes.c_float * n_samps.value)(*np.zeros(n_samps.value))
        yI = (ctypes.c_float * n_samps.value)(*np.zeros(n_samps.value))
        yQ = (ctypes.c_float * n_samps.value)(*np.zeros(n_samps.value))

        ## calls to c code
        cfm.fm_modulate(mod_factor, n_samps, x, xI, xQ, verbose, seed)
        cchan.channel(snr, n_samps, sps, fo, po, xI, xQ, yI, yQ, verbose, seed)

        metadata = {
            "modname": mod[-1],
            "modclass": mod[0],
            "modvariant": mod[1],
            "mod_factor": mod_factor.value,
            "n_samps": n_samps.value - buf,
            "channel_type": config["channel_type"],
            "snr": snr.value,
            "fo": fo.value,
            "po": po.value,
            "savepath": config["savepath"],
            "savename": config["savename"],
        }

        ## convert to numpy arrays
        I = np.array([_i for _i in yI])
        I = I[halfbuf:-halfbuf]
        Q = np.array([_q for _q in yQ])
        Q = Q[halfbuf:-halfbuf]

        ## save record in sigmf format
        save_sigmf(I, Q, metadata, idx_start + i)

    return idx_start + i + 1


def generate_fsk(idx_start, mod, config):
    verbose = ctypes.c_int(config["verbose"])
    modtype = ctypes.c_int(mod[0])
    n_samps = ctypes.c_int(config["n_samps"] + buf)

    sig_params = [
        (_sps, _beta, _delay, _dt)
        for _sps in config["symbol_rate"]
        for _beta in config["gaussian_filter"]["beta"]
        for _delay in config["gaussian_filter"]["delay"]
        for _dt in config["gaussian_filter"]["dt"]
    ]
    idx = np.random.choice(len(sig_params), config["n_captures"])
    sig_params = [sig_params[_idx] for _idx in idx]
    idx = np.random.choice(len(config["channel_params"]), config["n_captures"])
    channel_params = [config["channel_params"][_idx] for _idx in idx]

    for i in range(0, int(config["n_captures"])):
        seed = ctypes.c_int(rng_seed)
        snr = ctypes.c_float(channel_params[i][0])
        fo = ctypes.c_float(2.0 * channel_params[i][1] * np.pi)
        po = ctypes.c_float(0.0)  ## assume po = 0.0

        bps = ctypes.c_int(int(np.log2(mod[1])))
        modidx = ctypes.c_float(mod[2])
        sps = ctypes.c_int(sig_params[i][0])
        n_sym = n_sym = ctypes.c_int(int(np.ceil(n_samps.value / sps.value)))
        pulseshape = ctypes.c_int(mod[3])

        beta = ctypes.c_float(sig_params[i][1])
        delay = ctypes.c_uint(int(sig_params[i][2]))
        dt = ctypes.c_float(sig_params[i][3])

        ## create return arrays
        s = (ctypes.c_uint * n_sym.value)(*np.zeros(n_sym.value, dtype=int))
        xI = (ctypes.c_float * n_samps.value)(*np.zeros(n_samps.value))
        xQ = (ctypes.c_float * n_samps.value)(*np.zeros(n_samps.value))
        yI = (ctypes.c_float * n_samps.value)(*np.zeros(n_samps.value))
        yQ = (ctypes.c_float * n_samps.value)(*np.zeros(n_samps.value))

        ## calls to c code
        cfsk.fsk_modulate(
            n_sym, bps, modidx, pulseshape, sps, delay, beta, s, xI, xQ, verbose, seed
        )
        cchan.channel(snr, n_sym, sps, fo, po, xI, xQ, yI, yQ, verbose, seed)

        if mod[2] == 0.5:
            cs = 2.5e3
        elif mod[2] == 1.0:
            cs = 5e3
        elif mod[2] == 15.0:
            cs = 15e3
        else:
            cs = None

        if pulseshape.value == 0:
            ft = "square"
            b = "none"
        else:
            ft = "gaussian"
            b = beta.value

        metadata = {
            "modname": mod[-1],
            "modclass": mod[0],
            "order": mod[1],
            "mod_idx": modidx.value,
            "carrier_spacing": cs,
            "n_samps": n_samps.value - buf,
            "channel_type": config["channel_type"],
            "snr": snr.value,
            "filter_type": ft,
            "sps": sps.value,
            "beta": b,
            "delay": delay.value,
            "dt": dt.value,
            "fo": fo.value,
            "po": po.value,
            "savepath": config["savepath"],
            "savename": config["savename"],
        }

        ## convert to numpy arrays
        I = np.array([_i for _i in yI])
        I = I[halfbuf:-halfbuf]
        Q = np.array([_q for _q in yQ])
        Q = Q[halfbuf:-halfbuf]

        ## save record in sigmf format
        save_sigmf(I, Q, metadata, idx_start + i)

    return idx_start + i + 1


def generate_noise(idx_start, mod, config):
    verbose = ctypes.c_int(config["verbose"])
    modtype = ctypes.c_int(mod[0])
    n_samps = ctypes.c_int(config["n_samps"] + buf)

    idx = np.random.choice(len(config["channel_params"]), config["n_captures"])
    channel_params = [config["channel_params"][_idx] for _idx in idx]

    for i in range(0, config["n_captures"]):
        seed = ctypes.c_int(rng_seed)
        snr = ctypes.c_float(channel_params[i][0])
        fo = ctypes.c_float(2.0 * channel_params[i][1] * np.pi)
        po = ctypes.c_float(0.0)
        sps = ctypes.c_int(1)

        xI = (ctypes.c_float * n_samps.value)(*np.zeros(n_samps.value))
        xQ = (ctypes.c_float * n_samps.value)(*np.zeros(n_samps.value))
        yI = (ctypes.c_float * n_samps.value)(*np.zeros(n_samps.value))
        yQ = (ctypes.c_float * n_samps.value)(*np.zeros(n_samps.value))

        cchan.channel(snr, n_samps, sps, fo, po, xI, xQ, yI, yQ, verbose, seed)

        metadata = {
            "modname": mod[-1],
            "modclass": modtype.value,
            "n_samps": n_samps.value - buf,
            "channel_type": config["channel_type"],
            "snr": snr.value,
            "sps": sps.value,
            "fo": fo.value,
            "po": po.value,
            "savepath": config["savepath"],
            "savename": config["savename"],
        }

        ## convert to numpy arrays
        I = np.array([_i for _i in yI])
        I = I[halfbuf:-halfbuf]
        Q = np.array([_q for _q in yQ])
        Q = Q[halfbuf:-halfbuf]

        ## save record in sigmf format
        save_sigmf(I, Q, metadata, idx_start + i)

    return idx_start + i + 1


def run_tx(config):
    idx = 0

    ## loop through config
    for _mod in config["modulation"]:
        start_idx = idx
        if mod_int2modem[_mod[0]] is None:
            idx = generate_noise(start_idx, _mod, config)
        elif mod_int2modem[_mod[0]] == "linear":
            idx = generate_linear(start_idx, _mod, config)
        elif mod_int2modem[_mod[0]] == "amplitude":
            idx = generate_am(start_idx, _mod, config)
        elif mod_int2modem[_mod[0]] == "frequency":
            idx = generate_fm(start_idx, _mod, config)
        elif mod_int2modem[_mod[0]] == "freq_shift":
            idx = generate_fsk(start_idx, _mod, config)
        else:
            raise ValueError("Undefined modem.")

        print(_mod[-1] + ": " + str(idx - start_idx))

    if config["archive"]:
        archive_sigmf(config["savepath"])


if __name__ == "__main__":

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "config_file",
        type=str,
        help="Path to configuration file to use for data generation.",
    )
    parser.add_argument(
        "rng_seed",
        type=int,
        nargs="?",
        help="Random seed for data generation.",
    )
    args = parser.parse_args()

    with open(args.config_file) as f:
        config = json.load(f)

    # If a rng seed is provided, use it. Otherwise, use the one from the config file.
    if args.rng_seed is not None:
        rng_seed = args.rng_seed
    else:
        rng_seed = system_parameters["Random_Seed"]
    np.random.seed(rng_seed)

    with open("./configs/defaults.json") as f:
        defaults = json.load(f)

    config = map_config(config, defaults, dataset_directory)

    run_tx(config)
