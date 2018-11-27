from __future__ import print_function
from lib.can_actions import int_from_str_base, ARBITRATION_ID_MIN, ARBITRATION_ID_MAX, ARBITRATION_ID_MAX_EXTENDED
from lib.iso15765_2 import IsoTp
from lib.iso14229_1 import Iso14229_1, NegativeResponseCodes, Services
from sys import stdout, version_info
import argparse
import datetime
import time

# Handle large ranges efficiently in both python 2 and 3
if version_info[0] == 2:
    range = xrange

UDS_SERVICE_NAMES = {
    0x10: "DIAGNOSTIC_SESSION_CONTROL",
    0x11: "ECU_RESET",
    0x14: "CLEAR_DIAGNOSTIC_INFORMATION",
    0x19: "READ_DTC_INFORMATION",
    0x20: "RETURN_TO_NORMAL",
    0x22: "READ_DATA_BY_IDENTIFIER",
    0x23: "READ_MEMORY_BY_ADDRESS",
    0x24: "READ_SCALING_DATA_BY_IDENTIFIER",
    0x27: "SECURITY_ACCESS",
    0x28: "COMMUNICATION_CONTROL",
    0x2A: "READ_DATA_BY_PERIODIC_IDENTIFIER",
    0x2C: "DYNAMICALLY_DEFINE_DATA_IDENTIFIER",
    0x2D: "DEFINE_PID_BY_MEMORY_ADDRESS",
    0x2E: "WRITE_DATA_BY_IDENTIFIER",
    0x2F: "INPUT_OUTPUT_CONTROL_BY_IDENTIFIER",
    0x31: "ROUTINE_CONTROL",
    0x34: "REQUEST_DOWNLOAD",
    0x35: "REQUEST_UPLOAD",
    0x36: "TRANSFER_DATA",
    0x37: "REQUEST_TRANSFER_EXIT",
    0x38: "REQUEST_FILE_TRANSFER",
    0x3D: "WRITE_MEMORY_BY_ADDRESS",
    0x3E: "TESTER_PRESENT",
    0x7F: "NEGATIVE_RESPONSE",
    0x83: "ACCESS_TIMING_PARAMETER",
    0x84: "SECURED_DATA_TRANSMISSION",
    0x85: "CONTROL_DTC_SETTING",
    0x86: "RESPONSE_ON_EVENT",
    0x87: "LINK_CONTROL"
}

NRC_NAMES = {
    0x00: "POSITIVE_RESPONSE",
    0x10: "GENERAL_REJECT",
    0x11: "SERVICE_NOT_SUPPORTED",
    0x12: "SUB_FUNCTION_NOT_SUPPORTED",
    0x13: "INCORRECT_MESSAGE_LENGTH_OR_INVALID_FORMAT",
    0x14: "RESPONSE_TOO_LONG",
    0x21: "BUSY_REPEAT_REQUEST",
    0x22: "CONDITIONS_NOT_CORRECT",
    0x24: "REQUEST_SEQUENCE_ERROR",
    0x25: "NO_RESPONSE_FROM_SUBNET_COMPONENT",
    0x26: "FAILURE_PREVENTS_EXECUTION_OF_REQUESTED_ACTION",
    0x31: "REQUEST_OUT_OF_RANGE",
    0x33: "SECURITY_ACCESS_DENIED",
    0x35: "INVALID_KEY",
    0x36: "EXCEEDED_NUMBER_OF_ATTEMPTS",
    0x37: "REQUIRED_TIME_DELAY_NOT_EXPIRED",
    0x70: "UPLOAD_DOWNLOAD_NOT_ACCEPTED",
    0x71: "TRANSFER_DATA_SUSPENDED",
    0x72: "GENERAL_PROGRAMMING_FAILURE",
    0x73: "WRONG_BLOCK_SEQUENCE_COUNTER",
    0x78: "REQUEST_CORRECTLY_RECEIVED_RESPONSE_PENDING",
    0x7E: "SUB_FUNCTION_NOT_SUPPORTED_IN_ACTIVE_SESSION",
    0x7F: "SERVICE_NOT_SUPPORTED_IN_ACTIVE_SESSION",
    0x81: "RPM_TOO_HIGH",
    0x82: "RPM_TOO_LOW",
    0x83: "ENGINE_IS_RUNNING",
    0x84: "ENGINE_IS_NOT_RUNNING",
    0x85: "ENGINE_RUN_TIME_TOO_LOW",
    0x86: "TEMPERATURE_TOO_HIGH",
    0x87: "TEMPERATURE_TOO_LOW",
    0x88: "VEHICLE_SPEED_TOO_HIGH",
    0x89: "VEHICLE_SPEED_TOO_LOW",
    0x8A: "THROTTLE_PEDAL_TOO_HIGH",
    0x8B: "THROTTLE_PEDAL_TOO_LOW",
    0x8C: "TRANSMISSION_RANGE_NOT_IN_NEUTRAL",
    0x8D: "TRANSMISSION_RANGE_NOT_IN_GEAR",
    0x8F: "BRAKE_SWITCHES_NOT_CLOSED",
    0x90: "SHIFT_LEVER_NOT_IN_PARK",
    0x91: "TORQUE_CONVERTER_CLUTCH_LOCKED",
    0x92: "VOLTAGE_TOO_HIGH",
    0x93: "VOLTAGE_TOO_LOW"
}

REQUEST_DELAY = 0.01
BYTE_MIN = 0x00
BYTE_MAX = 0xFF
VALID_SESSION_CONTROL_RESPONSES = [0x50, 0x7F]


def auto_blacklist(tp, duration, print_results):
    """
    Blacklists the arbitration ID of messages which could be misinterpreted as valid diagnostic responses

    :param tp: transport protocol
    :param duration: int duration in seconds
    :param print_results: bool indicating whether results should be printed to stdout
    """
    if print_results:
        print("Scanning for arbitration IDs to blacklist (-autoblacklist)")
    ids_to_blacklist = set()
    start_time = time.time()
    end_time = start_time + duration
    while time.time() < end_time:
        if print_results:
            print("\r{0:> 5.1f} seconds left, {1} found".format(
                end_time - time.time() + 0, len(ids_to_blacklist)), end="")  # Adding zero prevents negative zero "-0.0"
            stdout.flush()
        msg = tp.bus.recv(0.1)
        if msg is None:
            continue
        if len(msg.data) >= 2 and msg.data[1] in VALID_SESSION_CONTROL_RESPONSES:
            ids_to_blacklist.add(msg.arbitration_id)
    if print_results:
        print("\n  Detected IDs: {0}".format(" ".join(sorted(list(map(hex, ids_to_blacklist))))))
    return ids_to_blacklist


def uds_discovery(min_id=None, max_id=None, blacklist_args=None, auto_blacklist_duration=0, delay=0.01,
                  print_results=True):
    """
    Scans for diagnostics support by brute forcing session control messages to different arbitration IDs

    :param min_id: start arbitration ID value
    :param max_id: end arbitration ID value
    :param blacklist_args: blacklist for arbitration ID values
    :param auto_blacklist_duration: seconds to scan for interfering arbitration IDs to blacklist automatically
    :param delay: delay between each message
    :param print_results: bool indicating whether results should be printed to stdout
    :return: list of (client_arbitration_id, server_arbitration_id) pairs
    """
    # Set defaults
    if min_id is None:
        min_id = ARBITRATION_ID_MIN
    if max_id is None:
        if min_id <= ARBITRATION_ID_MAX:
            max_id = ARBITRATION_ID_MAX
        else:
            # If min_id is extended, use an extended default max_id as well
            max_id = ARBITRATION_ID_MAX_EXTENDED
    if auto_blacklist_duration is None:
        auto_blacklist_duration = 0
    if blacklist_args is None:
        blacklist_args = []

    # Sanity checks
    if max_id < min_id:
        raise ValueError("max_id must not be smaller than min_id - got min:0x{0:x}, max:0x{1:x}".format(min_id, max_id))
    if auto_blacklist_duration < 0:
        raise ValueError("auto_blacklist_duration must not be smaller than 0, got {0}'".format(auto_blacklist_duration))

    service_id = Services.DiagnosticSessionControl.service_id
    sub_function = Services.DiagnosticSessionControl.DiagnosticSessionType.DEFAULT_SESSION
    session_control_data = [service_id, sub_function]

    found_arbitration_ids = []

    with IsoTp(None, None) as tp:
        blacklist = set(blacklist_args)
        # Perform automatic blacklist scan
        if auto_blacklist_duration > 0:
            auto_blacklist_arb_ids = auto_blacklist(tp, auto_blacklist_duration, print_results)
            blacklist |= auto_blacklist_arb_ids
        
        # Prepare session control frame
        session_control_frames = tp.get_frames_from_message(session_control_data)
        for send_arbitration_id in range(min_id, max_id + 1):
            if print_results:
                print("\rSending Diagnostic Session Control to 0x{0:04x}".format(send_arbitration_id), end="")
                stdout.flush()
            # Send Diagnostic Session Control
            tp.transmit(session_control_frames, send_arbitration_id, None)
            end_time = time.time() + delay
            # Listen for response
            while time.time() < end_time:
                msg = tp.bus.recv(0)
                if msg is None:
                    # No response received
                    continue
                if msg.arbitration_id in blacklist:
                    # Ignore blacklisted arbitration IDs
                    continue
                if len(msg.data) >= 2 and msg.data[1] in VALID_SESSION_CONTROL_RESPONSES:
                    # Valid response
                    if print_results:
                        print("\nFound diagnostics at arbitration ID 0x{0:04x}, response at 0x{1:04x}".format(
                            send_arbitration_id, msg.arbitration_id))
                    found_arb_id_pair = (send_arbitration_id, msg.arbitration_id)
                    found_arbitration_ids.append(found_arb_id_pair)
        if print_results:
            print()
    return found_arbitration_ids


def uds_discovery_wrapper(args):
    """
    Wrapper used to initiate a UDS discovery scan

    :return: list of (client_arbitration_id, server_arbitration_id) pairs
    :param args: namespace containing min, max, blacklist, autoblacklist and delay
    """
    min_id = int_from_str_base(args.min)
    max_id = int_from_str_base(args.max)
    blacklist = [int_from_str_base(b) for b in args.blacklist]
    auto_blacklist_duration = args.autoblacklist
    delay = args.delay
    print_results = True

    try:
        arb_id_pairs = uds_discovery(min_id, max_id, blacklist, auto_blacklist_duration, delay, print_results)
        if len(arb_id_pairs) == 0:
            # No UDS discovered
            print("\nDiagnostics service could not be found.")
        else:
            # Print result table
            print()
            table_line = "+------------+------------+"
            print(table_line)
            print("| CLIENT ID  | SERVER ID  |")
            print(table_line)
            for (client_id, server_id) in arb_id_pairs:
                print("| 0x{0:08x} | 0x{1:08x} |".format(client_id, server_id))
            print(table_line)
    except ValueError as e:
        print("Discovery failed: {0}".format(e))


def service_discovery(arb_id_request, arb_id_response, request_delay, min_id=BYTE_MIN, max_id=BYTE_MAX,
                      print_results=True):
    """
    Scans for supported UDS services on the specified arbitration ID

    :return: list of supported service IDs
    :param arb_id_request: int arbitration ID for requests
    :param arb_id_response: int arbitration ID for responses
    :param request_delay: float delay between each request sent
    :param min_id: int first service ID to scan
    :param max_id: int last service ID to scan
    :param print_results: bool indicating whether progress should be printed to stdout
    """
    found_services = []

    with IsoTp(arb_id_request=arb_id_request, arb_id_response=arb_id_response) as tp:
        # Setup filter for incoming messages
        tp.set_filter_single_arbitration_id(arb_id_response)
        # Send requests
        for service_id in range(min_id, max_id + 1):
            tp.send_request([service_id])
            if print_results:
                print("\rProbing service 0x{0:02x} ({0}/{1}) found {2}".format(
                    service_id, BYTE_MAX, len(found_services)), end="")
            stdout.flush()
            time.sleep(request_delay)
            # Get response
            msg = tp.bus.recv(0.1)
            if msg is None:
                # No response received
                continue
            # Parse response
            if len(msg.data) >= 3:
                service_id = msg.data[2]
                status = msg.data[3]
                if status != NegativeResponseCodes.SERVICE_NOT_SUPPORTED:
                    # Any other response than "service not supported" counts
                    found_services.append(service_id)
        if print_results:
            print("\n")
    return found_services


def service_discovery_wrapper(args):
    """
    Wrapper used to initiate a service discovery scan

    :return: list of supported service IDs
    :param args: A namespace containing src and dst
    """
    arb_id_request = int_from_str_base(args.src)
    arb_id_response = int_from_str_base(args.dst)
    request_delay = args.delay
    # Probe services
    found_services = service_discovery(arb_id_request, arb_id_response, request_delay)
    # Print results
    for service_id in found_services:
        service_name = UDS_SERVICE_NAMES.get(service_id, "Unknown service")
        print("Supported service 0x{0:02x}: {1}".format(service_id, service_name))


def tester_present(send_arb_id, delay, duration, suppress_positive_response):
    """
    Sends TesterPresent messages

    :param send_arb_id: int arbitration ID for requests
    :param delay: float seconds between each request
    :param duration: float seconds before automatically stopping or None to continue until stopped manually
    :param suppress_positive_response: bool indicating whether positive responses should be suppressed
    """

    # SPR simply tells the recipient not to send a positive response to each TesterPresent message
    if suppress_positive_response:
        sub_function = 0x80
    else:
        sub_function = 0x00

    # Calculate end timestamp if the TesterPresent should automatically stop after a given duration
    auto_stop = duration is not None
    end_time = None
    if auto_stop:
        end_time = datetime.datetime.now() + datetime.timedelta(seconds=duration)

    service_id = Services.TesterPresent.service_id
    message_data = [service_id, sub_function]
    print("Sending TesterPresent to arbitration ID {0} (0x{0:02x})".format(send_arb_id))
    print("\nPress Ctrl+C to stop\n")
    with IsoTp(send_arb_id, None) as can_wrap:
        counter = 1
        while True:
            can_wrap.send_request(message_data)
            print("\rCounter:", counter, end="")
            stdout.flush()
            time.sleep(delay)
            counter += 1
            if auto_stop and datetime.datetime.now() >= end_time:
                break


def tester_present_wrapper(args):
    """
    Wrapper used to initiate a TesterPresent session

    :param args: argparse.Namespace instance
    """
    send_arb_id = int_from_str_base(args.src)
    delay = args.delay
    duration = args.duration
    suppress_positive_response = args.spr

    tester_present(send_arb_id, delay, duration, suppress_positive_response)


def ecu_reset(arb_id_request, arb_id_response, reset_type, timeout):
    """
    Sends an ECU Reset message

    :return: list of response byte values on success, None otherwise
    :param arb_id_request: int arbitration ID for requests
    :param arb_id_response: int arbitration ID for responses
    :param reset_type: int value corresponding to a reset type
    :param timeout: float seconds to wait for response before timeout
    """
    # Sanity checks
    if not BYTE_MIN <= reset_type <= BYTE_MAX:
        raise ValueError("reset type must be within interval 0x{0:02x}-0x{1:02x}".format(BYTE_MIN, BYTE_MAX))
    if isinstance(timeout, float) and timeout < 0.0:
        raise ValueError("timeout value ({0}) cannot be negative".format(timeout))

    with IsoTp(arb_id_request=arb_id_request, arb_id_response=arb_id_response) as tp:
        # Setup filter for incoming messages
        tp.set_filter_single_arbitration_id(arb_id_response)
        with Iso14229_1(tp) as uds:
            # Set timeout
            if timeout is not None:
                uds.P3_CLIENT = timeout

            response = uds.ecu_reset(reset_type=reset_type)
            return response


def ecu_reset_wrapper(args):
    """
    Wrapper used to initiate ECUReset

    :param args: argparse.Namespace instance
    """
    arb_id_request = int_from_str_base(args.src)
    arb_id_response = int_from_str_base(args.dst)
    reset_type = int_from_str_base(args.reset_type)
    timeout = args.timeout

    print("Sending ECU reset, type 0x{0:02x} to arbitration ID {1} (0x{1:02x})".format(reset_type, arb_id_request))
    try:
        response = ecu_reset(arb_id_request, arb_id_response, reset_type, timeout)
    except ValueError as e:
        print("ValueError: {0}".format(e))
        return

    # Decode response
    if response is None:
        print("No response was received")
    else:
        response_length = len(response)
        if response_length == 0:
            # Empty response
            print("Received empty response")
        elif response_length == 1:
            # Invalid response length
            print("Received response [{0:02x}] (1 byte), expected at least 2 bytes".format(response[0], len(response)))
        elif Iso14229_1.is_positive_response(response):
            # Positive response handling
            response_service_id = response[0]
            subfunction = response[1]
            expected_response_id = Iso14229_1.get_service_response_id(Services.EcuReset.service_id)
            if response_service_id == expected_response_id and subfunction == reset_type:
                # Positive response
                print("Received positive response")
                if response_length > 2:
                    # Additional data can be seconds left to reset (powerDownTime) or manufacturer specific
                    additional_data = ",".join(["{0:02x}".format(b) for b in response[2:]])
                    print("Response contains additional data: [{0}]".format(additional_data))
            else:
                # Service and/or subfunction mismatch
                print("Response service ID 0x{0:02x} and subfunction 0x{1:02x} do not match expected values "
                      "0x{2:02x} and 0x{3:02x}".format(response_service_id, subfunction, expected_response_id,
                                                       reset_type))
        else:
            # Negative response handling
            nrc = response[1]
            nrc_description = NRC_NAMES.get(nrc, "Unknown NRC value")
            print("Received negative response code (NRC) 0x{0:02x}: {1}".format(nrc, nrc_description))


def parse_args(args):
    """
    Parser for diagnostics module arguments.

    :return: Namespace containing action and action-specific arguments
    :rtype: argparse.Namespace
    """
    parser = argparse.ArgumentParser(prog="cc.py uds",
                                     formatter_class=argparse.RawDescriptionHelpFormatter,
                                     description="""Universal Diagnostic Services module for CaringCaribou""",
                                     epilog="""Example usage:
  cc.py uds discovery
  cc.py uds discovery -blacklist 0x123 0x456
  cc.py uds discovery -autoblacklist 10
  cc.py uds services 0x733 0x633
  cc.py uds ecu_reset 1 0x733 0x633
  cc.py uds testerpresent 0x733""")
    subparsers = parser.add_subparsers(dest="module_function")
    subparsers.required = True

    # Parser for diagnostics discovery
    parser_discovery = subparsers.add_parser("discovery")
    parser_discovery.add_argument("-min", default=None)
    parser_discovery.add_argument("-max", default=None)
    parser_discovery.add_argument("-blacklist", metavar="B", default=[], nargs="+", help="arbitration IDs to ignore")
    parser_discovery.add_argument("-autoblacklist", metavar="N", type=int, default=0,
                                  help="scan for interfering signals for N seconds and blacklist matching "
                                       "arbitration IDs")
    parser_discovery.add_argument("--delay", type=float, default=REQUEST_DELAY, help="delay between each message")
    parser_discovery.set_defaults(func=uds_discovery_wrapper)

    # Parser for diagnostics service discovery
    parser_info = subparsers.add_parser("services")
    parser_info.add_argument("src", help="arbitration ID to transmit from")
    parser_info.add_argument("dst", help="arbitration ID to listen to")
    parser_info.add_argument("--delay", type=float, default=REQUEST_DELAY, help="delay between each message")
    parser_info.set_defaults(func=service_discovery_wrapper)

    # Parser for ECUReset
    parser_ecu_reset = subparsers.add_parser("ecu_reset")
    parser_ecu_reset.add_argument("reset_type", metavar="type",
                                  help="Reset type: 1=hard, 2=key off/on, 3=soft, 4=enable rapid power shutdown, "
                                       "5=disable rapid power shutdown")
    parser_ecu_reset.add_argument("src", help="arbitration ID to transmit from")
    parser_ecu_reset.add_argument("dst", help="arbitration ID to listen to")
    parser_ecu_reset.add_argument("-t", "--timeout", type=float, metavar="T",
                                  help="seconds to wait for response before timeout")
    parser_ecu_reset.set_defaults(func=ecu_reset_wrapper)

    # Parser for TesterPresent
    parser_tp = subparsers.add_parser("testerpresent")
    parser_tp.add_argument("src", help="arbitration ID to transmit from")
    parser_tp.add_argument("-delay", type=float, default=0.5, help="delay between each TesterPresent message")
    parser_tp.add_argument("-dur", "--duration", metavar="D", type=float, help="automatically stop after D seconds")
    parser_tp.add_argument("-spr", action="store_true", help="suppress positive response")
    parser_tp.set_defaults(func=tester_present_wrapper)

    args = parser.parse_args(args)
    return args


def module_main(arg_list):
    try:
        args = parse_args(arg_list)
        args.func(args)
    except KeyboardInterrupt:
        print("\n\nTerminated by user")
