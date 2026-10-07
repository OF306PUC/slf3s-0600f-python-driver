# -*- coding: utf-8 -*-
# (c) Copyright 2019 Sensirion AG, Switzerland
import time
from command import ShdlcCommand

import logging
log = logging.getLogger(__name__)

    
class ShdlcCmdI2cTransceiveBase(ShdlcCommand): 
    """
    SHDLC command ID: 0x2A "I2C Transceive".
    """

    def __init__(self, *args, **kwargs): 
        super(ShdlcCmdI2cTransceiveBase, self).__init__(
            0x2A, *args, **kwargs
        )

    def crc8_checksum_calculation(self, data): 
        """
        TODO: (finish understanding) Calculate CRC8 checksum for I2C data.

        :param bytes-like data: Data to calculate the checksum for.
        :return byte: Calculated CRC8 checksum.
        """
        crc = 0xFF
        for byte in data:
            crc ^= byte
            for _ in range(8):
                if (crc & 0x80) != 0:
                    crc = (crc << 1) ^ 0x31
                else:
                    crc <<= 1
                crc &= 0xFF  # Ensure CRC remains 8-bit
        return crc


class ShdlcCmdI2cTransceive(ShdlcCmdI2cTransceiveBase):
    """
    SHDLC command for I2C transceive operations.
    - Write data to an I2C device in big-endian order.
    - Read data from an I2C device in big-endian order. (9 bytes max for SLF3S-0600F sensor)
    """
    
    _I2C_TIMEOUT_MS = [0x00, 0x64]  # 100 ms timeout
    _I2C_ADDRESS = 0x08             # Default I2C address
    _MEDIUM_WATER = [0x36, 0x08]    # Medium: H20 calibration
    _MEDIUM_IPA = [0x36, 0x15]      # Medium: Isopropyl Alcohol calibration 
    _STOP_CODE = [0x3F, 0xF9]       # I2C Stop code

    _RELIABLE_FLOW_MEAS_DELAY_MS = 60  # ms

    # CHECK: https://www.nxp.com/docs/en/user-guide/UM10204.pdf
    _WRITE_BIT = 0x00
    _READ_BIT = 0x01

    def __init__(self, i2c_addr, tx_data, rx_length, i2c_timeout, max_response_time,
                 post_processing_time=0.0): 
        """
        Constructor.

        :param byte i2c_addr: I2C Slave Address.
        :param bytes-like/list tx_data: Data to write to the I2C device.
        :param int rx_length: Number of bytes to read from the I2C device.
        :param float max_response_time: Maximum time the device needs to
                                        response (used as timeout).
        :param float post_processing_time: Maximum time in seconds the device
                                           needs for post processing
                                           (typically 0.0s).
        """
        data = bytearray()
        data.append(i2c_addr)
        data.append(len(tx_data))
        data.append(rx_length)
        data.extend(i2c_timeout)
        data.extend(tx_data)

        super(ShdlcCmdI2cTransceive, self).__init__(
            data=data,
            max_response_time=max_response_time,
            min_response_length=0,
            max_response_length=9, # given by SLF3S-0600F datasheet max I2C read length 
            post_processing_time=post_processing_time
        )
        self.i2c_addr = i2c_addr
        self.tx_data = tx_data
        self.rx_length = rx_length

    def interpret_response(self, data): 
        """
        Interpret the I2C transceive response data from the device.

        :return int16 flow_raw: Raw flow value from the sensor. two's complement [-32768,32767]
        :return int16 temp_raw: Raw temperature value from the sensor. two's complement [-32768,32767]
        :return bool air_in_line_flag: Air-in-line event.
        :return bool high_flow_flag: High-flow event.
        """
        
        if self.rx_length == 0: 
            time.sleep(self._RELIABLE_FLOW_MEAS_DELAY_MS / 1000.0)
            return None  # No data to interpret
        
        if self.rx_length and len(data) != self.rx_length: 
            raise ValueError("I2C transceive: Expected {} bytes, got {} bytes."
                             .format(self.rx_length, len(data)))

        if self.crc8_checksum_calculation(data[0:2]) != data[2]:
            raise ValueError("CRC8 checksum error for flow data.")
        if self.crc8_checksum_calculation(data[3:5]) != data[5]:
            raise ValueError("CRC8 checksum error for temperature data.")
        if self.crc8_checksum_calculation(data[6:8]) != data[8]:
            raise ValueError("CRC8 checksum error for signaling flags data.")
        
        # value = (MSB << 8) | LSB: big-endian format
        flow_raw  = (data[0] << 8) | data[1]
        temp_raw  = (data[3] << 8) | data[4]
        flags_raw = (data[6] << 8) | data[7]

        return flow_raw, temp_raw, flags_raw

    
