# -*- coding: utf-8 -*-
# (c) Copyright 2019 Sensirion AG, Switzerland

class ShdlcSerialFrameBuilder(object): 
    """
    Base class for
    :py:class:`~sensirion_shdlc_driver.serial_frame_builder.ShdlcSerialMosiFrameBuilder`
    and
    :py:class:`~sensirion_shdlc_driver.serial_frame_builder.ShdlcSerialMisoFrameBuilder`.
    """

    _START_STOP_BYTE = 0x7E
    _ESCAPE_BYTE = 0x7D
    _ESCAPE_XOR = 0x20
    _CHARS_TO_ESCAPE = [_START_STOP_BYTE, _ESCAPE_BYTE, 0x11, 0x13]

    # Maximum raw frame length when all bytes are stuffed:
    # START + 2 * (ADDRESS + COMMAND + STATE + LENGTH + DATA + CHECKSUM) + STOP
    # = 1 + 2 * (1 + 1 + 1 + 1 + 255 + 1) + 1
    # = 522
    # " 2 * " due byte stuffing for those bytes inside the frame 
    _MAX_RAW_FRAME_LENGTH = 522

    def __init__(self):
        """
        Constructor.
        """
        super(ShdlcSerialFrameBuilder, self).__init__()

    @staticmethod
    def _calculate_checksum(frame):
        """
        Calculate the checksum for a frame.

        :param bytearray frame: Input frame.
        :return: Calculated checksum.
        :rtype: byte
        """
        return ~sum(frame) & 0xFF
    

class ShdlcSerialMosiFrameBuilder(ShdlcSerialFrameBuilder):
    """
    Serial MOSI (master out, slave in) frame builder: 

    | Start (0x7E) | Addr (1 byte) | Command (1 byte) | Len (1 byte) | Tx Data (0 ...255 bytes) | CRC (or CHK 1 byte) | Stop (0x7E) |

    This class allows to convert structured data (slave address, command ID
    etc.) into raw bytes which are then sent out via the serial port.
    """

    def __init__(self, slave_address, command_id, data):
        """
        Constructor.
        
        :param byte slave_address: Slave address.
        :param byte command_id: Specific command ID. (RS485 sensor cable - SHDLC Commands)
        :param bytearray data: Payload data. (can be empty: 0 bytes)
        """
        super(ShdlcSerialFrameBuilder, self).__init__()
        self._slave_address = int(slave_address) & 0xFF
        self._command_id = int(command_id) & 0xFF
        self._data = bytes(bytearray(data))

    @staticmethod
    def _stuff_data_bytes(data): 
        """
        Perform byte-stuffing (escape reserved bytes).

        :param bytearray data: The data without stuffed bytes.
        :return: The data with stuffed bytes.
        :rtype: bytearray
        """
        result = bytearray()
        for byte in data:
            if byte in ShdlcSerialFrameBuilder._CHARS_TO_ESCAPE:
                result.append(ShdlcSerialFrameBuilder._ESCAPE_BYTE)
                result.append(byte ^ ShdlcSerialFrameBuilder._ESCAPE_XOR)
            else:
                result.append(byte)
        return result

    def to_bytes(self): 
        """
        Convert the structured data from constructor to raw bytes.

        :return: Raw bytes to be sent via serial port.
        :rtype: bytes   
        """
        frame_content = bytearray([self._slave_address, self._command_id, len(self._data)]) + bytearray(self._data)
        frame_content.append(self._calculate_checksum(frame_content))
        
        raw_frame = bytearray() 
        raw_frame.append(self._START_STOP_BYTE)
        raw_frame.extend(self._stuff_data_bytes(frame_content))
        raw_frame.append(self._START_STOP_BYTE)
        return bytes(raw_frame)
    

class ShdlcSerialMisoFrameBuilder(ShdlcSerialFrameBuilder):
    """
    Serial MISO (master in, slave out) frame builder: 

    | Start (0x7E) | Addr (1 byte) | State (1 byte) | Len (1 byte) | Rx Data (0 ...255 bytes) | CRC (or CHK 1 byte) | Stop (0x7E) |

    This class allows to convert raw bytes received from the serial port into
    structured data (slave address, state ID etc.).
    """

    def __init__(self):
        """
        Constructor.
        """
        super(ShdlcSerialFrameBuilder, self).__init__()
        self._data = bytearray()

    @property
    def data(self): 
        """
        Get the received data.

        :return: The received data.
        :rtype: bytearray
        """
        return self._data
    
    @property
    def start_received(self):
        """
        Check if the start byte was already received.

        :return: Whether the start byte was already received or not.
        :rtype: bool
        """
        return self._START_STOP_BYTE in self._data
    
    @staticmethod
    def _unstuff_data_bytes(stuffed_data): 
        """
        Undo byte-stuffing (replacing stuffed bytes by their original value).

        :param bytearray stuffed_data: The data with stuffed bytes.
        :return: The data without stuffed bytes.
        :rtype: bytearray
        """
        data = bytearray()
        xor = 0x00
        for i in range(0, len(stuffed_data)):
            if stuffed_data[i] == ShdlcSerialFrameBuilder._ESCAPE_BYTE:
                xor = ShdlcSerialFrameBuilder._ESCAPE_XOR   # detects escape byte (0x7D)
            else:
                data.append(stuffed_data[i] ^ xor)          # next byte is xor'ed if previous was escape byte
                xor = 0x00
        return data
    
    def add_data(self, data): 
        """
        Adds more data (received from serial port) to internal buffer and check 
        if a complete frame has been received.

        :param bytearray data: New data received from serial port.
        :return: Whether a complete frame has been received or not.
        :rtype: bool
        """
        self._data.extend(bytearray(data))

        # checks if _START_STOP_BYTE = 0x7E appears at least twice
        if self._data.count(bytearray([self._START_STOP_BYTE])) >= 2:
            return True 
        # abort condition in case we are receiving endless rubbish
        elif len(self._data) > self._MAX_RAW_FRAME_LENGTH:
            raise RuntimeError("Received data exceeds maximum frame length.")
        
        else: 
            # incomplete frame
            return False
        
    def interpret_data(self):
        """
        Interpret and validate received raw data and return it.

        :return: Received slave address, command_id, state, and payload.
        :rtype: byte, byte, byte, bytes
        """

        separator = bytearray([self._START_STOP_BYTE])
        stuffed = self._data.split(separator)[1]       # [1] gets the data between the two 0x7E bytes
        unstuffed = self._unstuff_data_bytes(stuffed)
        if len(unstuffed) < 5:
            raise RuntimeError("Received data is too short.")
        
        frame = unstuffed[:-1]
        slv_addr = int(frame[0])
        command_id = int(frame[1])
        state = int(frame[2])
        length = int(frame[3])
        data = bytes(frame[4:])
        checksum = int(unstuffed[-1])
        if length != len(data):
            raise RuntimeError("Received data has wrong length. Length received: {}, expected: {}.".format(len(data), length))
        if checksum != self._calculate_checksum(frame):
            raise RuntimeError("Received data has wrong checksum. Checksum received: {}, expected: {}.".format(checksum, self._calculate_checksum(frame)))
        return slv_addr, command_id, state, data

            