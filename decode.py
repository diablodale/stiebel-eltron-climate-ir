#!/usr/bin/env python3
import sys
import re
import math

def main():
    # Read all input lines
    input_data = sys.stdin.read()

    # Find bit patterns of the form "10101010 00001111 ..."
    # (8 groups of 8 bits followed by a group of 5 bits)
    pattern_regex = r'(\d{8}\s+\d{8}\s+\d{8}\s+\d{8}\s+\d{8}\s+\d{8}\s+\d{8}\s+\d{8}\s+\d{5})'
    patterns = re.findall(pattern_regex, input_data)

    if not patterns:
        print("No matching bit patterns found in input. Looking for 8 bytes + 5 bits pattern.")
        return

    print(f"Found {len(patterns)} bit patterns to analyze.")

    # Process each pattern
    had_errors = False
    for i, pattern in enumerate(patterns):
        print(f"\n=== Processing Pattern {i+1} ===")
        print(pattern)

        # Remove all spaces
        pattern_no_spaces = pattern.replace(" ", "")

        # Discard the first 5 bits (preamble)
        data_bits = pattern_no_spaces[5:]

        # Decode the command
        success = decode_command(data_bits, i+1)
        if not success:
            had_errors = True

    # Exit with error code if any pattern failed
    if had_errors:
        print("\n❌ Some patterns had invalid checksums. See details above.")
        sys.exit(1)

def decode_command(data_bits, pattern_number):
    """Decode a Stiebel Eltron ACP 35 command."""

    # Extract fields according to the protocol specification
    celsius_offset = int(data_bits[0:4], 2)
    timer_active = int(data_bits[4:5], 2)
    bit_5 = int(data_bits[5:6], 2)
    power = int(data_bits[6:7], 2)
    zeros_1 = int(data_bits[7:11], 2)
    hours = int(data_bits[11:16], 2)
    zeros_2 = int(data_bits[16:19], 2)
    fahrenheit_offset = int(data_bits[19:24], 2)
    zeros_3 = int(data_bits[24:40], 2)
    zeros_4 = int(data_bits[40:42], 2)
    fan_speed = int(data_bits[42:44], 2)
    zeros_5 = int(data_bits[44:46], 2)
    mode = int(data_bits[46:48], 2)
    temp_units = int(data_bits[48:49], 2)
    zeros_6 = int(data_bits[49:54], 2)
    timer_ui = int(data_bits[54:55], 2)
    bit_55 = int(data_bits[55:56], 2)

    # The last 8 bits are the checksum
    checksum_bits = data_bits[56:64]
    expected_checksum = int(checksum_bits, 2)

    # Convert to bytes for checksum calculation
    # Exclude the preamble bits which are not part of checksum calculation
    command_bytes = []
    for j in range(0, 56, 8):
        byte_bits = data_bits[j:j+8]
        if len(byte_bits) == 8:  # Ensure we have a full byte
            byte_val = int(byte_bits, 2)
            command_bytes.append(byte_val)

    # Calculate checksum (initialize with 0x55, sum all bytes, truncate to 8 bits)
    calculated_checksum = calculate_checksum(command_bytes)
    checksum_valid = calculated_checksum == expected_checksum

    # Abort if checksum is invalid
    if not checksum_valid:
        print(f"\n❌ ERROR: Checksum verification failed for pattern {pattern_number}")
        print(f"  Expected checksum: 0x{expected_checksum:02X}")
        print(f"  Calculated checksum: 0x{calculated_checksum:02X}")
        print("  Command bytes: " + " ".join([f"0x{b:02X}" for b in command_bytes]))
        print("  Skipping further processing of this pattern.")
        return False

    # Display decoded information
    print("\n✅ Checksum Valid")
    print(f"  Expected: 0x{expected_checksum:02X}, Calculated: 0x{calculated_checksum:02X}")

    print("\n=== Command Decoded ===")

    # Temperature
    celsius_temp = celsius_offset + 16
    fahrenheit_temp = fahrenheit_offset + 59

    if temp_units == 1:  # Celsius
        primary_temp = f"{celsius_temp}°C"
        secondary_temp = f"{round(celsius_to_fahrenheit(celsius_temp))}°F"
    else:  # Fahrenheit
        primary_temp = f"{fahrenheit_temp}°F"
        secondary_temp = f"{round(fahrenheit_to_celsius(fahrenheit_temp))}°C"

    # Power state
    power_state = "ON" if power == 1 else "OFF"

    # Mode
    mode_names = ["Auto", "Cool", "Dry", "Fan"]
    mode_name = mode_names[mode] if 0 <= mode < len(mode_names) else f"Unknown ({mode})"

    # Fan speed
    fan_speeds = ["Auto", "Low", "Medium", "High"]
    if mode == 2:  # Dry mode forces fan to Low
        fan_speed_name = "Low (forced by Dry mode)"
    else:
        fan_speed_name = fan_speeds[fan_speed] if 0 <= fan_speed < len(fan_speeds) else f"Unknown ({fan_speed})"

    # Timer
    timer_status = "Active" if timer_active == 1 else "Off"
    timer_hours = hours if timer_active == 1 else 0
    timer_ui_display = "Timer UI" if timer_ui == 1 else "Standard UI"

    # Print decoded values
    print(f"Power: {power_state}")
    print(f"Mode: {mode_name}")
    print(f"Temperature: {primary_temp} ({secondary_temp})")
    print(f"Temperature Unit: {'Celsius' if temp_units == 1 else 'Fahrenheit'}")
    print(f"Fan Speed: {fan_speed_name}")
    print(f"Timer: {timer_status}, {timer_hours} hours")
    print(f"Display: {timer_ui_display}")

    # Print raw values for verification
    print("\n=== Raw Values ===")
    print(f"Celsius Offset: {celsius_offset} (Temp: {celsius_temp}°C)")
    print(f"Fahrenheit Offset: {fahrenheit_offset} (Temp: {fahrenheit_temp}°F)")
    print(f"Power Bit: {power}")
    print(f"Mode Bits: {mode:02b} ({mode})")
    print(f"Fan Speed Bits: {fan_speed:02b} ({fan_speed})")
    print(f"Temperature Unit Bit: {temp_units}")
    print(f"Timer Active Bit: {timer_active}")
    print(f"Timer Hours: {hours}")
    print(f"Timer UI Bit: {timer_ui}")

    # Print binary format for verification
    print("\n=== Binary Verification ===")
    print(f"Celsius: {data_bits[0:4]} (offset {celsius_offset})")
    print(f"Timer: {data_bits[4:5]} ({'Active' if timer_active else 'Off'})")
    print(f"Bit5: {data_bits[5:6]}")
    print(f"Power: {data_bits[6:7]} ({'ON' if power else 'OFF'})")
    print(f"Zeros1: {data_bits[7:11]}")
    print(f"Hours: {data_bits[11:16]} ({hours})")
    print(f"Zeros2: {data_bits[16:19]}")
    print(f"Fahrenheit: {data_bits[19:24]} (offset {fahrenheit_offset})")
    print(f"Zeros3: {data_bits[24:40]}")
    print(f"Zeros4: {data_bits[40:42]}")
    print(f"Fan: {data_bits[42:44]} ({fan_speed})")
    print(f"Zeros5: {data_bits[44:46]}")
    print(f"Mode: {data_bits[46:48]} ({mode})")
    print(f"Temp Units: {data_bits[48:49]} ({'C' if temp_units else 'F'})")
    print(f"Zeros6: {data_bits[49:54]}")
    print(f"Timer UI: {data_bits[54:55]} ({'Timer UI' if timer_ui else 'Standard UI'})")
    print(f"Bit55: {data_bits[55:56]}")
    print(f"Checksum: {data_bits[56:64]} (0x{expected_checksum:02X})")

    return True  # Indicate successful processing

def calculate_checksum(command_bytes):
    """Calculate the Stiebel Eltron ACP 35 checksum.
    Initialize with 0x55, sum all bytes, truncate to 8 bits.
    """
    checksum = 0x55  # Initialize with 0x55
    for byte in command_bytes:
        checksum = (checksum + byte) & 0xFF  # Add byte and truncate to 8 bits
    return checksum

def celsius_to_fahrenheit(celsius):
    """Convert Celsius to Fahrenheit."""
    return (celsius * 9/5) + 32

def fahrenheit_to_celsius(fahrenheit):
    """Convert Fahrenheit to Celsius."""
    return (fahrenheit - 32) * 5/9

if __name__ == "__main__":
    main()