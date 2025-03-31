#!/usr/bin/env python3
import sys
import re
import numpy as np

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
    all_command_bytes = []
    all_expected_checksums = []

    for i, pattern in enumerate(patterns):
        print(f"\n=== Processing Pattern {i+1} ===")
        print(pattern)

        # Remove all spaces
        pattern_no_spaces = pattern.replace(" ", "")

        # Discard the first 5 bits
        data_bits = pattern_no_spaces[5:]

        # Split into 8 bytes (groups of 8 bits)
        command_bytes = []
        for j in range(0, 64, 8):  # Process 8 bytes (64 bits)
            if j+8 <= len(data_bits):
                byte_bits = data_bits[j:j+8]
                byte_val = int(byte_bits, 2)  # MSB first
                command_bytes.append(byte_val)

        # The last byte (8th byte) is the expected checksum
        if len(command_bytes) == 8:
            expected_checksum = command_bytes.pop()  # Remove and use the last byte as checksum

            print(f"Command bytes: {[f'0x{b:02X}' for b in command_bytes]}")
            print(f"Expected checksum: 0x{expected_checksum:02X}")

            # Perform initial checksum tests
            test_checksums(command_bytes, expected_checksum)
            test_additional_checksums(command_bytes, expected_checksum)

            # Store for cross-validation
            all_command_bytes.append(command_bytes)
            all_expected_checksums.append(expected_checksum)
        else:
            print(f"Warning: Expected 8 bytes but found {len(command_bytes)}")

    # If we have multiple commands, verify algorithms across all of them
    if len(all_command_bytes) >= 2:
        print("\n=== ALGORITHM VERIFICATION ACROSS ALL PATTERNS ===")
        verify_algorithms_across_all(all_command_bytes, all_expected_checksums)

def test_checksums(command_bytes, expected_checksum):
    """Test various checksum algorithms on a single command."""
    print("\n=== BASIC CHECKSUM TESTS ===")

    # 1. XOR of all bytes
    xor_result = 0
    for byte in command_bytes:
        xor_result ^= byte
    print(f"XOR of all bytes: 0x{xor_result:02X} {'✓' if xor_result == expected_checksum else '✗'}")

    # 2. Sum of all bytes (8-bit, truncated)
    sum_result = sum(command_bytes) & 0xFF
    print(f"Sum of all bytes (8-bit): 0x{sum_result:02X} {'✓' if sum_result == expected_checksum else '✗'}")

    # 3. XOR of sum with 0xFF (one's complement)
    inverse_sum = (sum(command_bytes) & 0xFF) ^ 0xFF
    print(f"Inverse of sum (XOR with 0xFF): 0x{inverse_sum:02X} {'✓' if inverse_sum == expected_checksum else '✗'}")

    # 4. XOR of sum with 0x7F (common in some protocols)
    xor_with_7f = (sum(command_bytes) & 0xFF) ^ 0x7F
    print(f"Sum XOR with 0x7F: 0x{xor_with_7f:02X} {'✓' if xor_with_7f == expected_checksum else '✗'}")

    # 5. 8-bit two's complement of sum
    twos_complement = ((sum(command_bytes) & 0xFF) ^ 0xFF) + 1
    twos_complement &= 0xFF  # Ensure it's 8-bit
    print(f"Two's complement of sum: 0x{twos_complement:02X} {'✓' if twos_complement == expected_checksum else '✗'}")

    # 6. Find potential XOR matches
    found_matches = False
    for xor_value in range(0, 256):
        result = (sum(command_bytes) & 0xFF) ^ xor_value
        if result == expected_checksum:
            print(f"Sum XOR with 0x{xor_value:02X}: 0x{result:02X} ✓")
            found_matches = True

    if not found_matches:
        print("No exact XOR matches found")

    # 7. Test if it's the sum of all but the first byte
    partial_sum = sum(command_bytes[1:]) & 0xFF
    print(f"Sum of all bytes except first: 0x{partial_sum:02X} {'✓' if partial_sum == expected_checksum else '✗'}")

    # 8. Test if it's the sum of all but the last byte
    if len(command_bytes) > 1:
        partial_sum = sum(command_bytes[:-1]) & 0xFF
        print(f"Sum of all bytes except last: 0x{partial_sum:02X} {'✓' if partial_sum == expected_checksum else '✗'}")

def test_additional_checksums(command_bytes, expected_checksum):
    """Test additional, more complex checksum algorithms."""
    print("\n=== ADDITIONAL CHECKSUM TESTS ===")

    # 1. Test different starting values (common in CRCs)
    for init in [0x00, 0x01, 0x05, 0x0A, 0x55, 0xAA, 0xFF]:
        checksum = init
        for byte in command_bytes:
            checksum = (checksum + byte) & 0xFF
        print(f"Sum with init 0x{init:02X}: 0x{checksum:02X} {'✓' if checksum == expected_checksum else '✗'}")

    # 2. Rolling XOR with different initial values
    for init in [0x00, 0x01, 0x05, 0x0A, 0x55, 0xAA, 0xFF]:
        checksum = init
        for byte in command_bytes:
            checksum = (checksum ^ byte) & 0xFF
        print(f"Rolling XOR with init 0x{init:02X}: 0x{checksum:02X} {'✓' if checksum == expected_checksum else '✗'}")

    # 3. Test rotating bits in addition
    checksum = 0
    for byte in command_bytes:
        checksum = ((checksum << 1) | (checksum >> 7)) & 0xFF  # Rotate left
        checksum = (checksum + byte) & 0xFF
    print(f"Rotate left + sum: 0x{checksum:02X} {'✓' if checksum == expected_checksum else '✗'}")

    checksum = 0
    for byte in command_bytes:
        checksum = ((checksum >> 1) | (checksum << 7)) & 0xFF  # Rotate right
        checksum = (checksum + byte) & 0xFF
    print(f"Rotate right + sum: 0x{checksum:02X} {'✓' if checksum == expected_checksum else '✗'}")

    # 4. Test byte-specific weights (often used in checksums)
    for multiplier in range(1, 8):
        checksum = 0
        for i, byte in enumerate(command_bytes):
            checksum = (checksum + (byte * ((i + multiplier) % 8 + 1))) & 0xFF
        print(f"Weighted sum (mult={multiplier}): 0x{checksum:02X} {'✓' if checksum == expected_checksum else '✗'}")

    # 5. Test sums of specific subsets of bytes
    # First and last bytes only
    if len(command_bytes) > 1:
        checksum = (command_bytes[0] + command_bytes[-1]) & 0xFF
        print(f"Sum of first and last bytes: 0x{checksum:02X} {'✓' if checksum == expected_checksum else '✗'}")

    # 6. Linear combination with XOR
    for mask in [0x55, 0xAA, 0xF0, 0x0F]:
        checksum = 0
        for byte in command_bytes:
            checksum = (checksum + (byte & mask) + (byte & ~mask)) & 0xFF
        print(f"Linear combination with mask 0x{mask:02X}: 0x{checksum:02X} {'✓' if checksum == expected_checksum else '✗'}")

    # 7. Bitwise operations on sum
    sum_result = sum(command_bytes) & 0xFF
    for shift in range(1, 8):
        # Left rotate sum
        rotated = ((sum_result << shift) | (sum_result >> (8 - shift))) & 0xFF
        print(f"Sum rotated left by {shift}: 0x{rotated:02X} {'✓' if rotated == expected_checksum else '✗'}")

        # Right rotate sum
        rotated = ((sum_result >> shift) | (sum_result << (8 - shift))) & 0xFF
        print(f"Sum rotated right by {shift}: 0x{rotated:02X} {'✓' if rotated == expected_checksum else '✗'}")

    # 8. Test for inverted bits in specific positions
    sum_result = sum(command_bytes) & 0xFF
    for pos in range(8):
        # Flip a specific bit in the sum
        flipped = sum_result ^ (1 << pos)
        print(f"Sum with bit {pos} flipped: 0x{flipped:02X} {'✓' if flipped == expected_checksum else '✗'}")

def verify_algorithms_across_all(all_commands, all_checksums):
    """Verify which algorithms work across all command sets."""
    print("\n=== VERIFYING ALGORITHMS ACROSS ALL COMMANDS ===")

    # Dictionary to store algorithm results
    algorithm_results = {}

    # Add all basic algorithms to check
    for cmd_idx, (cmd, expected) in enumerate(zip(all_commands, all_checksums)):
        # Add all algorithms to test
        algorithms = {
            # 1. XOR of all bytes
            "XOR of all bytes": xor_all(cmd),

            # 2. Sum of all bytes
            "Sum of all bytes": sum(cmd) & 0xFF,

            # 3. XOR of sum with 0xFF
            "Inverse of sum (XOR with 0xFF)": (sum(cmd) & 0xFF) ^ 0xFF,

            # 4. XOR of sum with 0x7F
            "Sum XOR with 0x7F": (sum(cmd) & 0xFF) ^ 0x7F,

            # 5. Two's complement of sum
            "Two's complement of sum": ((sum(cmd) & 0xFF) ^ 0xFF + 1) & 0xFF,

            # 6. Sum excluding first byte
            "Sum excluding first byte": sum(cmd[1:]) & 0xFF if len(cmd) > 1 else 0,

            # 7. Sum excluding last byte
            "Sum excluding last byte": sum(cmd[:-1]) & 0xFF if len(cmd) > 1 else 0,

            # 8. Sum of first and last bytes
            "Sum of first and last bytes": (cmd[0] + cmd[-1]) & 0xFF if len(cmd) > 1 else 0,

            # 10. Rotate left + sum
            "Rotate left + sum": rotate_left_sum(cmd),

            # 11. Rotate right + sum
            "Rotate right + sum": rotate_right_sum(cmd),
        }

        # Add all possible XOR values
        for xor_val in range(0, 256):
            algorithms[f"Sum XOR with 0x{xor_val:02X}"] = (sum(cmd) & 0xFF) ^ xor_val

        # Add various init values for rolling sum
        for init in [0x00, 0x01, 0x05, 0x0A, 0x55, 0xAA, 0xFF]:
            algorithms[f"Sum with init 0x{init:02X}"] = rolling_sum(cmd, init)
            algorithms[f"Rolling XOR with init 0x{init:02X}"] = rolling_xor(cmd, init)

        # Add sum rotations
        sum_val = sum(cmd) & 0xFF
        for shift in range(1, 8):
            algorithms[f"Sum rotated left by {shift}"] = ((sum_val << shift) | (sum_val >> (8 - shift))) & 0xFF
            algorithms[f"Sum rotated right by {shift}"] = ((sum_val >> shift) | (sum_val << (8 - shift))) & 0xFF

        # Add bit flips
        for pos in range(8):
            algorithms[f"Sum with bit {pos} flipped"] = sum_val ^ (1 << pos)

        # Add weighted sums
        for multiplier in range(1, 8):
            algorithms[f"Weighted sum (mult={multiplier})"] = weighted_sum(cmd, multiplier)

        # Store results for this command
        for name, result in algorithms.items():
            if name not in algorithm_results:
                algorithm_results[name] = []
            algorithm_results[name].append((result, expected, result == expected))

    # Check which algorithms match for all commands
    all_matching = []

    print("\nAlgorithm                       | Results (Expected=Actual) | All Match?")
    print("-------------------------------|---------------------------|------------")

    for name, results in algorithm_results.items():
        all_match = all(match for _, _, match in results)

        # Only show detailed results for potentially matching algorithms
        if all_match or name.startswith("Sum XOR with") and any(match for _, _, match in results):
            result_str = " ".join([f"(0x{expected:02X}={'✓' if match else '✗'}0x{result:02X})"
                                for result, expected, match in results])

            print(f"{name:<30} | {result_str:<25} | {'✓' if all_match else '✗'}")

            if all_match:
                all_matching.append(name)

    # Report matches
    if all_matching:
        print("\nThe following algorithms matched ALL commands:")
        for name in all_matching:
            print(f"  ✓ {name}")
    else:
        print("\nNo algorithm matched ALL commands.")

        # Check for possible transformations or patterns
        print("\n=== CHECKING FOR ALGORITHM PATTERNS ===")

        # Get the sum of each command
        sums = [sum(cmd) & 0xFF for cmd in all_commands]

        # Check if the keys (sum ^ checksum) have a pattern
        keys = [sum_val ^ checksum for sum_val, checksum in zip(sums, all_checksums)]

        print("Command sums and derived keys:")
        for i, (sum_val, checksum, key) in enumerate(zip(sums, all_checksums, keys)):
            print(f"  Command {i+1}: Sum=0x{sum_val:02X}, Checksum=0x{checksum:02X}, Key=0x{key:02X}")

        # Check if the key might be derived from a specific byte in the command
        for byte_idx in range(min(len(cmd) for cmd in all_commands)):
            relations = []
            for cmd_idx, (cmd, key) in enumerate(zip(all_commands, keys)):
                byte_val = cmd[byte_idx]
                rel = byte_val ^ key
                relations.append((byte_val, key, rel))

            # If all relations are the same, we found a pattern
            if len(set(rel for _, _, rel in relations)) == 1:
                rel_val = relations[0][2]
                print(f"\nPATTERN FOUND: Byte at index {byte_idx} XOR {rel_val:02X} produces the key")
                for cmd_idx, (byte_val, key, _) in enumerate(relations):
                    print(f"  Command {cmd_idx+1}: 0x{byte_val:02X} ^ 0x{rel_val:02X} = 0x{key:02X}")

# Helper functions
def xor_all(bytes_list):
    """Calculate the XOR of all bytes."""
    result = 0
    for byte in bytes_list:
        result ^= byte
    return result

def rolling_sum(bytes_list, init_val):
    """Calculate a rolling sum with an initial value."""
    result = init_val
    for byte in bytes_list:
        result = (result + byte) & 0xFF
    return result

def rolling_xor(bytes_list, init_val):
    """Calculate a rolling XOR with an initial value."""
    result = init_val
    for byte in bytes_list:
        result = (result ^ byte) & 0xFF
    return result

def rotate_left_sum(bytes_list):
    """Calculate a sum with left rotation."""
    result = 0
    for byte in bytes_list:
        result = ((result << 1) | (result >> 7)) & 0xFF  # Rotate left
        result = (result + byte) & 0xFF
    return result

def rotate_right_sum(bytes_list):
    """Calculate a sum with right rotation."""
    result = 0
    for byte in bytes_list:
        result = ((result >> 1) | (result << 7)) & 0xFF  # Rotate right
        result = (result + byte) & 0xFF
    return result

def weighted_sum(bytes_list, multiplier):
    """Calculate a weighted sum."""
    result = 0
    for i, byte in enumerate(bytes_list):
        result = (result + (byte * ((i + multiplier) % 8 + 1))) & 0xFF
    return result

if __name__ == "__main__":
    main()