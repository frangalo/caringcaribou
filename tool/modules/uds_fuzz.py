from __future__ import print_function
from lib.common import list_to_hex_str, parse_int_dec_or_hex, str_to_int_list
from lib.iso14229_1 import Iso14229_1, NegativeResponseCodes
from modules.uds import ecu_reset, print_negative_response, request_seed, extended_session
from sys import stdout
import argparse
import time

# Number of seconds to wait between messages
DELAY_SECSEED_RESET = 0.011
DELAY_FUZZ_RESET = 3.901

# Message data length limits
MIN_DATA_LENGTH = 1
MAX_DATA_LENGTH = 8
# Max size of random seed if no seed is provided in arguments
DEFAULT_SEED_MAX = 2 ** 16
# Number of sub-lists to split message list into per round in 'replay' mode
REPLAY_NUMBER_OF_SUB_LISTS = 5
BYTE_MIN = 0x00
BYTE_MAX = 0xFF


# Duplicate testing from https://www.iditect.com/guide/python/python_howto_find_the_duplicates_in_a_list.html
def find_duplicates(sequence):
    first_seen = set()
    first_seen_add = first_seen.add
    duplicates = set(i for i in sequence if i in first_seen or first_seen_add(i))
    return duplicates


def request_seed_fuzzer(arb_id_request, arb_id_response, session_type, 
                        reset_type, reset_delay, nostop, sequence):
    """Fuzz the request of a seed (Security Access)"""
    # Range from 1 to 65 to avoid "Invalid request seed level"
    for level in range(1, 66, 2):
        # Reset ECU if reset type has been provided
        if reset_type:
            ecu_reset(arb_id_request, arb_id_response, reset_type, None)
            time.sleep(reset_delay)
        # Sends the seed request
        response = request_seed(arb_id_request, arb_id_response, level, None, None)
        # If the response is negative but the code is Response Pending or response is positive, found a sequence
        if response is not None and (response[2] == NegativeResponseCodes.REQUEST_CORRECTLY_RECEIVED_RESPONSE_PENDING or Iso14229_1.is_positive_response(response)):
            # Prints the sequence found
            sequence.append("0x27 {}".format(hex(level)))
            print("Sequence found: {}".format(sequence))
            sequence.pop()
            # No stop execution if --nostop or -ns has been supplied
            if not nostop:
                exit(0)


def session_sequence_fuzzer(arb_id_request, arb_id_response, reset_type, 
                            reset_delay, nostop, prev_session_types, sequence):
    """Recursive function to look for the session sequence needed to get the seed"""
    try:
        # Fuzz the extended session request (0x10 [0x01-0xFF])
        for session_type in range(1, 256):
            if session_type not in prev_session_types:
                response = extended_session(arb_id_request, arb_id_response, session_type)
                # If a positive is received, fuzz the seed request and repeat the process to find the sequence
                if Iso14229_1.is_positive_response(response):
                    # Save the context information
                    prev_session_types.add(session_type)
                    sequence.append("0x10 {}".format(hex(session_type)))
                    # Tries to find the seed
                    request_seed_fuzzer(arb_id_request, arb_id_response, session_type, reset_type, reset_delay, nostop, sequence)
                    session_sequence_fuzzer(arb_id_request, arb_id_response, reset_type, 
                                            reset_delay, nostop, prev_session_types, sequence)
                    # Remove last elements
                    prev_session_types.remove(session_type)
                    sequence.pop()

    except KeyboardInterrupt:
        print("Interrupted by user.")
        exit(1)
    except Exception as e:
        print(e)
        return


def __session_sequence_wrapper(args):
    """Wrapper used to initiate the session sequence fuzzer"""
    arb_id_request = args.src
    arb_id_response = args.dst
    reset_type = args.reset
    reset_delay = args.delay
    nostop = args.nostop

    # Set that will save the previous session types (avoid an infinite loop)
    prev_session_types = set()
    # List that will save the sequence needed to get a seed
    sequence = list()

    print("Session sequence fuzzer started. Press Ctrl+C if you need to stop.\n")
    session_sequence_fuzzer(arb_id_request, arb_id_response, reset_type, reset_delay, nostop, prev_session_types, sequence)


def seed_randomness_fuzzer(args):
    """Wrapper used to initiate security randomness fuzzer"""
    arb_id_request = args.src
    arb_id_response = args.dst
    reset_type = args.reset
    session_type = args.sess_type
    iterations = args.iter
    reset_delay = args.delay
    reset_method = args.reset_method
    inter = args.inter_delay

    seed_list = []
    try:

        # Issue first reset with the supplied delay time
        print("Security seed dump started. Press Ctrl+C if you need to stop.\n")
        ecu_reset(arb_id_request, arb_id_response, reset_type, None)
        time.sleep(reset_delay)
        for i in range(iterations):
            if reset_method == 1 and i > 0:
                ecu_reset(arb_id_request, arb_id_response, reset_type, None)
                time.sleep(reset_delay)

            for y in range(0, len(session_type), 4):

                # Get into the appropriate supplied session
                if session_type[y] == "1" and session_type[y + 1] == "0":
                    session = str_to_hex(y, session_type)
                    response = extended_session(arb_id_request,
                                                arb_id_response,
                                                session)
                    if not Iso14229_1.is_positive_response(response):
                        print("Unable to enter session. Retrying...\n")
                    if inter:
                        time.sleep(inter)

                # Request seed
                elif session_type[y] == "2" and session_type[y + 1] == "7":

                    session = str_to_hex(y, session_type)
                    response = request_seed(arb_id_request, arb_id_response,
                                            session, None, None)
                    if response is None:
                        print("\nInvalid response")
                    elif Iso14229_1.is_positive_response(response):
                        seed_list.append(list_to_hex_str(response[2:]))
                        print("Seed received: {}\t(Total captured: {})"
                              .format(list_to_hex_str(response[2:]),
                                      len(seed_list)), end="\r")

                        stdout.flush()
                    if inter:
                        time.sleep(inter)

                    else:
                        print_negative_response(response)
                        break

                # ECUReset
                elif session_type[y] == "1" and session_type[y + 1] == "1":
                    ecu_reset(arb_id_request, arb_id_response, int(session_type[y + 3]), None)
                    time.sleep(reset_delay)
                else:
                    print("\nPlease check your supplied sequence...")
                    break

    except KeyboardInterrupt:
        print("Interrupted by user.")
    except ValueError as e:
        print(e)
        return

    # Print captured seeds and found duplicates
    if len(seed_list) > 0:
        print("\n")
        print("Security Access Seeds captured:")
        for seed in seed_list:
            print(seed)
        print("\nDuplicates found: \n", find_duplicates(seed_list))


def delay_fuzzer(args):
    """Wrapper used to initiate delay fuzzer"""
    arb_id_request = args.src
    arb_id_response = args.dst
    reset_type = args.reset
    session_type = args.sess_type
    target = args.target_seed
    reset_delay = args.delay
    loop = True

    seed_list = []
    try:
        print("Security seed dump started. Press Ctrl+C to stop.\n")
        while loop:

            # Issue first reset with the supplied delay time
            ecu_reset(arb_id_request, arb_id_response, reset_type, None)
            time.sleep(reset_delay)

            # Loop through the length of the supplied input
            for i in range(0, len(session_type), 4):

                # Get into the appropriate supplied session
                if session_type[i] == "1" and session_type[i + 1] == "0":
                    session = str_to_hex(i, session_type)
                    response = extended_session(arb_id_request,
                                                arb_id_response,
                                                session)
                    if not Iso14229_1.is_positive_response(response):
                        print("Unable to enter session. Retrying...\n")
                        break

                # Request seed
                elif session_type[i] == "2" and session_type[i + 1] == "7":

                    session = str_to_hex(i, session_type)
                    response = request_seed(arb_id_request, arb_id_response,
                                            session, None, None)
                    if response is None:
                        print("\nInvalid response")
                    elif Iso14229_1.is_positive_response(response):
                        seed_list.append(list_to_hex_str(response[2:]))
                        print("Seed received: {}\t(Total captured: {}, Delay used: {})"
                              .format(list_to_hex_str(response[2:]),
                                      len(seed_list), reset_delay), end="\r")

                        if list_to_hex_str(response[2:]) == list_to_hex_str(str_to_int_list(target)):
                            print("\n\nTarget seed found with delay: ", reset_delay)
                            loop = False
                            break

                        stdout.flush()

                    else:
                        print_negative_response(response)
                        break

                # ECUReset
                elif session_type[i] == 1 and session_type[i + 1] == 1:
                    ecu_reset(arb_id_request, arb_id_response, reset_type, None)
                    time.sleep(reset_delay)
                else:
                    break

            # ECUReset and increase of delay in each loop
            if reset_type:
                ecu_reset(arb_id_request, arb_id_response, reset_type, None)
                time.sleep(reset_delay)
                reset_delay += 0.001

    except KeyboardInterrupt:
        print("Interrupted by user.")
    except ValueError as e:
        print(e)
        return

    if len(seed_list) > 0:
        print("\n")
        print("Security Access Seeds captured:")
        for seed in seed_list:
            print(seed)


def str_to_hex(i, session_type):
    max_index = i + 3
    if len(session_type) >= max_index:
        session = []
        session.append('0x')
        session.append(session_type[i + 2])
        session.append(session_type[i + 3])
        session = ''.join(session)
        session = int(session, 16)
        return session
    else:
        return


def __parse_args(args):
    """Parser for module arguments"""
    parser = argparse.ArgumentParser(
        prog="cc.py uds_fuzz",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="UDS seed randomness fuzzer and tester module for "
                    "CaringCaribou",
        epilog="""Example usage:
  cc.py uds_fuzz session_sequence_fuzzer 0x733 0x633 -r 1 -d 4 --nostop
  cc.py uds_fuzz seed_randomness_fuzzer 100311022701 0x733 0x633 -d 4 -r 1 -id 2 -m 0
  cc.py uds_fuzz delay_fuzzer 100311022701 0x03 0x733 0x633""")
    subparsers = parser.add_subparsers(dest="module_function")
    subparsers.required = True

    # Parser for Session sequence fuzz testing
    parser_session_sequence_fuzzer = subparsers.add_parser("session_sequence_fuzzer")
    parser_session_sequence_fuzzer.add_argument("src",
                                     type=parse_int_dec_or_hex,
                                     help="arbitration ID to transmit to")
    parser_session_sequence_fuzzer.add_argument("dst",
                                     type=parse_int_dec_or_hex,
                                     help="arbitration ID to listen to")
    parser_session_sequence_fuzzer.add_argument("-r", "--reset", metavar="RTYPE", default=None,
                                     type=parse_int_dec_or_hex,
                                     help="Enable reset between security seed "
                                          "requests. Valid RTYPE integers are: "
                                          "1=hardReset, 2=key off/on, 3=softReset, "
                                          "4=enable rapid power shutdown, "
                                          "5=disable rapid power shutdown. "
                                          "(default: None)")
    parser_session_sequence_fuzzer.add_argument("-d", "--delay", metavar="D",
                                     type=float, default=DELAY_SECSEED_RESET,
                                     help="Wait D seconds between reset and "
                                           "security seed request. You'll likely "
                                           "need to increase this when using RTYPE: "
                                           "1=hardReset. Does nothing if RTYPE "
                                           "is None. (default: {0})"
                                     .format(DELAY_FUZZ_RESET))
    parser_session_sequence_fuzzer.add_argument("-ns", "--nostop",
                                     action="store_true",
                                     help="Do not stop execution after the first sequence is found "
                                           "(search all available sequences)")
    parser_session_sequence_fuzzer.set_defaults(func=__session_sequence_wrapper)

    # Parser for Delay fuzz testing
    parser_delay_fuzzer = subparsers.add_parser("delay_fuzzer")
    parser_delay_fuzzer.add_argument("sess_type", metavar="stype",
                                     help="Describe the session sequence followed by "
                                          "the target ECU."
                                          "e.g. if the following sequence is needed in order to request a seed: "
                                          "Request 1 - 1003 (Diagnostic Session Control), "
                                          "Request 2 - 1102 (ECUReset), "
                                          "Request 3 - 1005 (Diagnostic Session Control), "
                                          "Request 4 - 2705 (Security Access Seed Request). "
                                          "The option should be: 1003110210052705\n")
    parser_delay_fuzzer.add_argument("target_seed", metavar="target",
                                     help="Seed that is targeted for the delay attack. "
                                          "e.g. 41414141414141")
    parser_delay_fuzzer.add_argument("src",
                                     type=parse_int_dec_or_hex,
                                     help="arbitration ID to transmit to")
    parser_delay_fuzzer.add_argument("dst",
                                     type=parse_int_dec_or_hex,
                                     help="arbitration ID to listen to")
    parser_delay_fuzzer.add_argument("-r", "--reset", metavar="RTYPE", default=1,
                                     type=parse_int_dec_or_hex,
                                     help="Enable reset between security seed "
                                          "requests. Valid RTYPE integers are: "
                                          "1=hardReset, 2=key off/on, 3=softReset, "
                                          "4=enable rapid power shutdown, "
                                          "5=disable rapid power shutdown. "
                                          "This attack is based on hard ECUReset (1) "
                                          "as it targets seed randomness based on "
                                          "the system clock. (default: hardReset)")
    parser_delay_fuzzer.add_argument("-d", "--delay", metavar="D",
                                     type=float, default=DELAY_SECSEED_RESET,
                                     help="Wait D seconds between the different "
                                          "iterations of security seed request. You'll "
                                          "likely need to increase this when using RTYPE: "
                                          "1=hardReset. (default: {0})"
                                     .format(DELAY_SECSEED_RESET))
    parser_delay_fuzzer.set_defaults(func=delay_fuzzer)

    # Parser for Delay fuzz testing
    parser_randomness_fuzzer = subparsers.add_parser("seed_randomness_fuzzer")
    parser_randomness_fuzzer.add_argument("sess_type", metavar="stype",
                                          help="Describe the session sequence followed by "
                                               "the target ECU."
                                               "e.g. if the following sequence is needed in order to request a seed: "
                                               "Request 1 - 1003 (Diagnostic Session Control), "
                                               "Request 2 - 1102 (ECUReset), "
                                               "Request 3 - 1005 (Diagnostic Session Control), "
                                               "Request 4 - 2705 (Security Access Seed Request). "
                                               "The option should be: 1003110210052705\n")
    parser_randomness_fuzzer.add_argument("src",
                                          type=parse_int_dec_or_hex,
                                          help="arbitration ID to transmit to")
    parser_randomness_fuzzer.add_argument("dst",
                                          type=parse_int_dec_or_hex,
                                          help="arbitration ID to listen to")
    parser_randomness_fuzzer.add_argument("-t", "--iter", metavar="ITERATIONS", default=1000,
                                          type=parse_int_dec_or_hex,
                                          help="Number of iterations of seed requests. "
                                               "It is highly suggested to perform >=1000  "
                                               "for accurate results. "
                                               "(default: 1000)")
    parser_randomness_fuzzer.add_argument("-r", "--reset", metavar="RTYPE", default=1,
                                          type=parse_int_dec_or_hex,
                                          help="Enable reset between security seed "
                                               "requests. Valid RTYPE integers are: "
                                               "1=hardReset, 2=key off/on, 3=softReset, "
                                               "4=enable rapid power shutdown, "
                                               "5=disable rapid power shutdown. "
                                               "This attack is based on hard ECUReset (1) "
                                               "as it targets seed randomness based on "
                                               "the system clock. (default: hardReset)")
    parser_randomness_fuzzer.add_argument("-id", "--inter_delay", metavar="RTYPE", default=0.1,
                                          type=float,
                                          help="Intermediate delay between messages:"
                                               "(default: 0.1)")
    parser_randomness_fuzzer.add_argument("-m", "--reset_method", metavar="RMETHOD", default=1,
                                          type=parse_int_dec_or_hex,
                                          help="The method that the ECUReset will happen: "
                                               "1=before each seed request "
                                               "0=once before the seed requests start "
                                               "(default: 1) *This method works better with option 1.*")
    parser_randomness_fuzzer.add_argument("-d", "--delay", metavar="D",
                                          type=float, default=DELAY_SECSEED_RESET,
                                          help="Wait D seconds between reset and "
                                               "security seed request. You'll likely "
                                               "need to increase this when using RTYPE: "
                                               "1=hardReset. Does nothing if RTYPE "
                                               "is None. (default: {0})"
                                          .format(DELAY_FUZZ_RESET))
    parser_randomness_fuzzer.set_defaults(func=seed_randomness_fuzzer)

    args = parser.parse_args(args)
    return args


def module_main(arg_list):
    """Module main wrapper"""
    try:
        args = __parse_args(arg_list)
        args.func(args)
    except KeyboardInterrupt:
        print("\n\nTerminated by user")
