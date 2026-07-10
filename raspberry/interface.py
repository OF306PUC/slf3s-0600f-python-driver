# -*- coding: utf-8 -*-
# (c) Copyright 2019 Sensirion AG, Switzerland
import time 

import logging
log = logging.getLogger(__name__)


class ShdlcInterface(object): 
    """
    This class represents the connection to an SHDLC RS485 Sensor Cable bus. 

    The basic functionality of the class is to send SHDLC frames to device and
    receive their response. Handling of communication errors (e.g. timeout or
    checksum errors) and device errors is done in this class.
    """

    _I2C_TIMEOUT_MS = [0x00, 0x64]  # 100 ms timeout

    def __init__(self, port):
        """
        Open an SHDLC connection on a specific port.

        .. note:: This constructor does not send or receive any data to resp.
                  from the specified port.

        :param ~sensirion_shdlc_driver.port.ShdlcPort port:
            The port used for communication (must implement the
            :py:class:`~sensirion_shdlc_driver.port.ShdlcPort` interface)
        """
        super(ShdlcInterface, self).__init__()
        self._port = port
        log.debug("Opened ShdlcConnection on '{}'.".format(port))

    @property
    def port(self):
        """
        Get the underlying port.

        :return: An :py:class:`~sensirion_shdlc_driver.port.ShdlcPort` object.
        :rtype: ~sensirion_shdlc_driver.port.ShdlcPort
        """
        return self._port
    
    def transceive(self, slave_address, command_id, data, response_timeout): 
        """
        Send a raw SHDLC command and return the received raw response.

        :param byte slave_address: Device (RS485) slave address.
        :param byte command_id: SHDLC command ID.
        :param bytes-like data: SHDLC command data (payload) (may be empty).
        :param float response_timeout: Response timeout in seconds (maximum
                                       time until the first byte is received).
        :return: Received response payload and error state flag.
        :rtype: bytes, bool
        """
        rx_addr, rx_cmd, rx_state, rx_data = self._port.transceive(
            slave_address, command_id, data, response_timeout)
        
        # print("--- interface.py ---")                                                                       ####
        # print("Received response - Addr: 0x{:02X}, Cmd: 0x{:02X}, State: 0x{:02X}, Data: [{}]".format(      ####
        #     rx_addr, rx_cmd, rx_state,                                                                      ####
        #     ", ".join(["0x{:02X}".format(b) for b in bytearray(rx_data)])))                                 ####

        if rx_addr != slave_address: 
            raise ValueError("Received response from unexpected slave address "
                            "(expected {}, got {}).".format(slave_address, rx_addr))
        if rx_cmd != command_id: 
            raise ValueError("Received response for unexpected command ID "
                            "(expected 0x{:02X}, got 0x{:02X}).".format(command_id, rx_cmd))
                            
        error_state = True if rx_state & 0x80 else False # 1000_0000
        if error_state:
            log.warning("SHDLC device with address {} is in error state.".format(slave_address))
        error_code = rx_state & 0x7F # 0111_1111
        if error_code: 
            log.warning("SHDLC device with address {} returned error {}."
                        .format(slave_address, error_code))
        return rx_data, error_state
    
    def execute(self, slave_address, command, wait_post_process=True): 
        """
        Execute an SHDLC command. Executing an SHDLC command means:

        - Send request (MOSI) frame
        - Receive response (MISO) frame
        - Validate and interpret response data
        - Wait until post processing is done (optional, and only if needed)

        :param byte slave_address:
            Slave address.
        :param ~command.ShdlcCommand command:
            The command to execute.
        :param bool wait_post_process:
            Whether to wait for post processing time after receiving the
            response. Some commands might need some time for post processing,
            this thread blocks until post processing is done.
        :return: The interpreted response data and error state flag.
        :rtype: tuple (object, bool)
        """
        data, error = self.transceive(slave_address, command.id, command.data, 
                                     command.max_response_time)
        if wait_post_process and command.post_processing_time > 0.0: 
            # Wait for post processing time in the device (to be sure that 
            # device is ready for next command)
            time.sleep(command.post_processing_time)
        command.check_response_length(data)  # Raises if length was wrong
        return command.interpret_response(data), error
    
        


        