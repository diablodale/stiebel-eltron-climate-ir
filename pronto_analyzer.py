#!/usr/bin/env python3

import re
import sys

def ensure_dependencies():
    """Check for required packages and install if missing, but ask user first."""
    required_packages = ['numpy', 'matplotlib']
    missing_packages = []

    # Check which packages are missing
    for package in required_packages:
        try:
            __import__(package)
        except ImportError:
            missing_packages.append(package)

    # Install missing packages if user agrees
    if missing_packages:
        import sys

        print(f"Missing dependencies: {', '.join(missing_packages)}")
        response = input("Do you want to install these packages now? (y/n): ").strip().lower()

        if response == 'y' or response == 'yes':
            import subprocess
            try:
                print(f"Installing: {' '.join(missing_packages)}")
                subprocess.check_call([sys.executable, "-m", "pip", "install"] + missing_packages)
                print("All dependencies installed successfully!")
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
        "raw_codes": pronto_codes
    }

    return analysis

def decode_to_binary(analysis):
    """
    Attempt to decode the timing data to binary.
    This is a simplified approach - actual decoding depends on the specific protocol.
    """
    timings = analysis["timings_us"]

    # Find typical on/off times by clustering
    on_times = [timings[i] for i in range(0, len(timings), 2)]
    off_times = [timings[i] for i in range(1, len(timings), 2)]

    # Simple approach: find the average short and long pulses
    on_times_sorted = sorted(on_times)
    off_times_sorted = sorted(off_times)

    # If there are enough samples, try to determine short vs long pulses
    binary_signal = []

    if len(off_times) > 10:
        # Separate short and long off-times
        # This is a simple approach - a better one would use clustering
        threshold = np.median(off_times_sorted) * 1.5

        for i in range(0, len(timings) - 1, 2):
            if i+1 < len(timings):
                if timings[i+1] > threshold:
                    binary_signal.append(1)  # long off-time = 1
                else:
                    binary_signal.append(0)  # short off-time = 0

    # Group bits into bytes for easier reading
    bytes_data = []
    for i in range(0, len(binary_signal), 8):
        if i + 8 <= len(binary_signal):
            byte = binary_signal[i:i+8]
            byte_val = 0
            for bit in byte:
                byte_val = (byte_val << 1) | bit
            bytes_data.append(f"{byte_val:02X}")

    return {
        "binary": binary_signal,
        "bytes": bytes_data
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
    # Ensure all dependencies are installed
    ensure_dependencies()

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

        print("\nPossible bytes (simplified interpretation):")
        print(' '.join(binary["bytes"]))

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