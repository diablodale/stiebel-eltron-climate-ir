#!/usr/bin/env python3

import re
import sys

def ensure_dependencies():
    """Check for required packages and install if missing, but ask user first."""
    required_packages = ['numpy', 'matplotlib', 'scikit-learn']
    missing_packages = []

    # Check which packages are missing
    for package in required_packages:
        try:
            if package == 'scikit-learn':
                __import__('sklearn')
            else:
                __import__(package)
        except ImportError:
            missing_packages.append(package)

    # Install missing packages if user agrees
    if missing_packages:
        print(f"Missing dependencies: {', '.join(missing_packages)}")
        response = input("Do you want to install these packages now? (y/n): ").strip().lower()

        if response == 'y' or response == 'yes':
            import subprocess
            try:
                print(f"Installing: {' '.join(missing_packages)}")
                subprocess.check_call([sys.executable, "-m", "pip", "install"] + missing_packages)
                print("All dependencies installed successfully!")

                # Important: Notify user about restarting the script
                print("Please restart the script for the changes to take effect.")
                sys.exit(0)
            except subprocess.CalledProcessError:
                print("Failed to install dependencies. Please install them manually:")
                print(f"pip install {' '.join(missing_packages)}")
                sys.exit(1)
        else:
            print("Dependencies required but installation skipped.")
            print(f"Please install manually with: pip install {' '.join(missing_packages)}")
            sys.exit(1)

# Run dependency check before importing numpy and matplotlib
ensure_dependencies()

# Now safe to import these
import numpy as np
import matplotlib.pyplot as plt
from sklearn.cluster import KMeans

def parse_pronto_log(log_lines):
    """Extract Pronto codes from log lines and return as a single list."""
    pronto_codes = []
    for line in log_lines:
        # Match everything after "remote.pronto:233]:" or similar pattern
        match = re.search(r'remote\.pronto:\d+]:\s+(.*)', line)
        if match:
            # Extract the hex values and add to our list
            codes = match.group(1).strip().split()
            pronto_codes.extend(codes)
    return pronto_codes

def analyze_pronto_codes(pronto_codes):
    """Analyze a list of Pronto codes."""
    # Basic Pronto format: [protocol, frequency, seq1_len, seq2_len, data...]
    if len(pronto_codes) < 4:
        return "Invalid Pronto code: too short"

    # Protocol - usually 0000 for raw IR with modulation
    protocol = pronto_codes[0]
    protocol_desc = {
        "0000": "Raw IR with modulation",
        "0100": "Raw IR without modulation",
        "5000": "RC5 protocol",
        "6000": "RC6 protocol",
        "900A": "NEC1 protocol"
    }.get(protocol, "Unknown protocol")

    # Frequency code - convert to Hz
    freq_code = int(pronto_codes[1], 16)
    frequency = 1000000 / (freq_code * 0.241246)

    # Sequence lengths
    seq1_len = int(pronto_codes[2], 16)
    seq2_len = int(pronto_codes[3], 16)

    # Calculate expected length and validate
    expected_length = 4 + seq1_len * 2 + seq2_len * 2  # Header (4) + seq1 pairs + seq2 pairs
    actual_length = len(pronto_codes)

    sequence_valid = actual_length == expected_length

    # Extract the timing data
    timing_data = [int(code, 16) for code in pronto_codes[4:]]

    # Convert timing to microseconds
    period_us = 1000000 / frequency
    timings_us = [val * period_us for val in timing_data]

    # Create analysis dictionary
    analysis = {
        "protocol": protocol,
        "protocol_desc": protocol_desc,
        "frequency_code": freq_code,
        "frequency_hz": frequency,
        "seq1_len": seq1_len,
        "seq2_len": seq2_len,
        "timing_data": timing_data,
        "timings_us": timings_us,
        "period_us": period_us,
        "raw_codes": pronto_codes,
        "sequence_valid": sequence_valid,
        "expected_length": expected_length,
        "actual_length": actual_length
    }

    return analysis

def decode_to_binary(analysis, lsb_first=False):
    """
    Attempt to decode the timing data to binary.
    Most IR protocols encode data as pairs of on/off pulses with varying durations.

    Parameters:
    - analysis: The analysis dictionary from analyze_pronto_codes
    - lsb_first: If True, interpret bits as LSB first instead of MSB first
    """
    timings = analysis["timings_us"]

    # Skip the first few pairs which are typically header/leader pulses
    # For many protocols, data starts after the header (often 4-6 pairs)
    start_idx = 8  # Skip first 4 pairs (8 values) as they're likely header pulses

    # Extract pairs of on/off pulses
    pairs = []
    for i in range(start_idx, len(timings) - 1, 2):
        if i + 1 < len(timings):
            pairs.append((timings[i], timings[i + 1]))

    if len(pairs) < 8:  # Need enough data to detect patterns
        return {"binary": [], "bytes": []}

    # Analyze the distribution of pulse pairs to find patterns
    # Calculate total duration of each pair (on + off time)
    pair_durations = [on + off for on, off in pairs]

    # Identify short and long pulses using clustering
    # Sort durations and find the midpoint between clusters
    sorted_durations = sorted(pair_durations)

    # Use the median as a starting point
    median_idx = len(sorted_durations) // 2
    median_value = sorted_durations[median_idx]

    # Find the largest gap in the sorted durations around the median
    max_gap = 0
    threshold = median_value

    for i in range(1, len(sorted_durations)):
        gap = sorted_durations[i] - sorted_durations[i-1]
        if gap > max_gap:
            max_gap = gap
            threshold = (sorted_durations[i] + sorted_durations[i-1]) / 2

    # Alternative approach: use K-means for 2 clusters
    try:
        # Reshape for KMeans
        X = np.array(pair_durations).reshape(-1, 1)
        kmeans = KMeans(n_clusters=2, random_state=0).fit(X)
        centers = kmeans.cluster_centers_.flatten()
        labels = kmeans.labels_

        # Map to binary: shorter durations = 0, longer durations = 1
        if centers[0] < centers[1]:
            binary_signal = [1 if label == 1 else 0 for label in labels]
        else:
            binary_signal = [1 if label == 0 else 0 for label in labels]
    except:
        # Fallback to threshold-based approach if K-means fails
        binary_signal = [1 if duration > threshold else 0 for duration in pair_durations]

    # Group bits into bytes for easier reading
    bytes_data_msb = []
    bytes_data_lsb = []

    for i in range(0, len(binary_signal), 8):
        if i + 8 <= len(binary_signal):
            byte = binary_signal[i:i+8]

            # MSB first (left to right)
            byte_val_msb = 0
            for bit in byte:
                byte_val_msb = (byte_val_msb << 1) | bit
            bytes_data_msb.append(f"{byte_val_msb:02X}")

            # LSB first (right to left)
            byte_val_lsb = 0
            for bit_idx in range(7, -1, -1):
                byte_val_lsb = (byte_val_lsb << 1) | byte[bit_idx]
            bytes_data_lsb.append(f"{byte_val_lsb:02X}")

    return {
        "binary": binary_signal,
        "bytes_msb": bytes_data_msb,
        "bytes_lsb": bytes_data_lsb,
        "bytes": bytes_data_msb if not lsb_first else bytes_data_lsb
    }

def visualize_signal(analysis):
    """Create a visualization of the IR signal."""
    timings = analysis["timings_us"]

    # Create a representation of the signal
    signal = []
    time = 0
    state = 1  # Start with ON

    signal_times = [0]
    signal_states = [0]

    for duration in timings:
        time += duration
        signal_times.append(time)
        signal_states.append(state)
        state = 1 - state  # Toggle between 0 and 1

    # Create a simple ASCII visualization
    ascii_viz = ""
    for i in range(min(50, len(timings))):
        if i % 2 == 0:  # ON pulse
            ascii_viz += "█" * min(40, int(timings[i] / 100))
        else:  # OFF pulse
            ascii_viz += "_" * min(40, int(timings[i] / 100))
        if i % 10 == 9:
            ascii_viz += "\n"

    return {
        "signal_times": signal_times,
        "signal_states": signal_states,
        "ascii_viz": ascii_viz
    }

def main():
    # Read from stdin if no arguments provided
    if sys.stdin.isatty():
        print("Please pipe IR log data to this script or provide a filename as argument.")
        print("Example: cat ir_log.txt | python pronto_analyzer.py")
        print("     or: python pronto_analyzer.py ir_log.txt")
        return

    # Read from stdin
    log_lines = sys.stdin.readlines()

    # Parse the log lines to get pronto codes
    pronto_codes = parse_pronto_log(log_lines)

    if not pronto_codes:
        print("No valid Pronto codes found in input. Make sure the input contains lines with '[remote.pronto:XXX]:' prefix.")
        return

    # Analyze the pronto codes
    analysis = analyze_pronto_codes(pronto_codes)

    # Print basic analysis
    print(f"Protocol: {analysis['protocol']} ({analysis['protocol_desc']})")
    print(f"Frequency: {analysis['frequency_hz']:.1f} Hz")
    print(f"Start sequence length: {analysis['seq1_len']} pairs")
    print(f"Repeat sequence length: {analysis['seq2_len']} pairs")

    # Add sequence validation info
    if 'sequence_valid' in analysis:
        if analysis['sequence_valid']:
            print(f"Sequence length validation: PASSED ({analysis['actual_length']} codes)")
        else:
            print(f"Sequence length validation: FAILED")
            print(f"  Expected {analysis['expected_length']} codes, got {analysis['actual_length']} codes")

    # Detailed timing analysis
    print("\nTiming analysis:")
    on_times = [analysis["timings_us"][i] for i in range(0, len(analysis["timings_us"]), 2)]
    off_times = [analysis["timings_us"][i] for i in range(1, len(analysis["timings_us"]), 2)]

    print(f"ON pulses - Min: {min(on_times):.1f}µs, Max: {max(on_times):.1f}µs, Avg: {sum(on_times)/len(on_times):.1f}µs")
    print(f"OFF pulses - Min: {min(off_times):.1f}µs, Max: {max(off_times):.1f}µs, Avg: {sum(off_times)/len(off_times):.1f}µs")

    # Attempt to decode to binary
    binary = decode_to_binary(analysis)
    if binary["binary"]:
        print("\nBinary representation (simplified):")
        binary_str = ''.join(map(str, binary["binary"]))
        # Print in groups of 8 for readability
        for i in range(0, len(binary_str), 8):
            print(binary_str[i:i+8], end=' ')
        print()

        print("\nBytes (MSB first interpretation):")
        print(' '.join(binary["bytes_msb"]))

        print("\nBytes (LSB first interpretation):")
        print(' '.join(binary["bytes_lsb"]))

    # Visualize the signal
    viz = visualize_signal(analysis)
    print("\nSignal visualization (first part):")
    print(viz["ascii_viz"])

    # Plot the signal
    plt.figure(figsize=(15, 5))
    plt.step(viz["signal_times"], viz["signal_states"], where='post')
    plt.ylabel('Signal Level')
    plt.xlabel('Time (microseconds)')
    plt.title('IR Signal Visualization')
    plt.grid(True)

    # Add markers for potential data bits
    if binary["binary"]:
        bit_times = []
        bit_values = []
        bit_idx = 0
        for i in range(1, len(analysis["timings_us"]), 2):
            if bit_idx < len(binary["binary"]):
                # Mark the middle of each bit period
                time_point = sum(analysis["timings_us"][:i+1])
                bit_times.append(time_point)
                bit_values.append(binary["binary"][bit_idx] * 0.5)  # Plot at half height
                bit_idx += 1

        plt.scatter(bit_times, bit_values, color='red', marker='o', label='Decoded Bits')
        plt.legend()

    plt.savefig('ir_signal.png')
    print("\nSignal plot saved as 'ir_signal.png'")

if __name__ == "__main__":
    main()