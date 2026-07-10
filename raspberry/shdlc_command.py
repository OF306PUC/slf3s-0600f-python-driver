from command import ShdlcCommand

import logging
log = logging.getLogger(__name__)


class ShdlcGetVersionBase(ShdlcCommand): 
    """
    SHDLC command ID: 0xD1 "Get Version".
    """

    def __init__(self, *args, **kwargs): 
        super(ShdlcGetVersionBase, self).__init__(
            0xD1, *args, **kwargs
        )

class ShdlcGetVersion(ShdlcGetVersionBase):
    def __init__(self): 
        super(ShdlcGetVersion, self).__init__(
            data=[], max_response_time=0.1,
            min_response_length=7, max_response_length=7
        )

    def interpret_response(self, data): 
        """
        :return dict: Version information with keys:
                      'major', 'minor', 'debug_state',
                      'hardware_major', 'hardware_minor',
                      'shdlc_protocol_major', 'shdlc_protocol_minor'.
        """
        data_bytes = bytearray(data)  
        version_major = int(data_bytes[0])
        version_minor = int(data_bytes[1])
        debug_state = bool(data_bytes[2])
        hardware_major = int(data_bytes[3])
        hardware_minor = int(data_bytes[4])
        shdlc_protocol_major = int(data_bytes[5])
        shdlc_protocol_minor = int(data_bytes[6])
        info = {
            'major': version_major,
            'minor': version_minor,
            'debug_state': debug_state,
            'hardware_major': hardware_major,
            'hardware_minor': hardware_minor,
            'shdlc_protocol_major': shdlc_protocol_major,
            'shdlc_protocol_minor': shdlc_protocol_minor
        }
        return info


class ShdlcDeviceSelfTestBase(ShdlcCommand):
    """
    SHDLC command ID: 0x22 "Device Self Test".
    """

    def __init__(self, *args, **kwargs): 
        super(ShdlcDeviceSelfTestBase, self).__init__(
            0x22, *args, **kwargs
        )

class ShdlcDeviceSelfTest(ShdlcDeviceSelfTestBase):
    def __init__(self): 
        super(ShdlcDeviceSelfTest, self).__init__(
            data=[], max_response_time=1.0,
            min_response_length=2, max_response_length=2
        )

    def interpret_response(self, data): 
        """
        Self test result code. uint16 [bit encoded]
        0: EEPROM error
        1: MCU supply voltage too high/low
        2: Failure on I2C line
        3: Failure on supply voltage line
        :return dict: Self test result with keys:
                      'eeprom_error', 'mcu_supply_voltage_error',
                      'i2c_line_error', 'supply_voltage_error'.
        """
        data_bytes = bytearray(data)  
        data_bytes = (data_bytes[0] << 8) | data_bytes[1]
        eeprom_error = bool(data_bytes & 0x0001)
        mcu_supply_voltage_error = bool(data_bytes & 0x0002)
        i2c_line_error = bool(data_bytes & 0x0004)
        supply_voltage_error = bool(data_bytes & 0x0008)
        result = {
            'eeprom_error': eeprom_error,
            'mcu_supply_voltage_error': mcu_supply_voltage_error,
            'i2c_line_error': i2c_line_error,
            'supply_voltage_error': supply_voltage_error
        }
        return result 
    

class ShdlcGetVoltageBase(ShdlcCommand): 
    """
    SHDLC command ID: 0x23 "Get Sensor Voltage".
    """

    def __init__(self, *args, **kwargs): 
        super(ShdlcGetVoltageBase, self).__init__(
            0x23, *args, **kwargs
        )

class ShdlcGetVoltage(ShdlcGetVoltageBase):
    def __init__(self): 
        super(ShdlcGetVoltage, self).__init__(
            data=[], max_response_time=0.1,
            min_response_length=1, max_response_length=1
        )

    def interpret_response(self, data): 
        """
        Voltage Setting : uint8t[0,1]
        0: Sensor Voltage = 3.5V
        1: Sensor Voltage = 5V
        :return int: Sensor voltage in millivolts.
        """
        data_bytes = bytearray(data)  
        if int(data_bytes[0]) == 0: 
            return 3.5
        else: 
            return 5.0
        

class ShdlcGetSensorTypeBase(ShdlcCommand):
    """
    SHDLC command ID: 0x24 "Get Sensor Type".
    """

    def __init__(self, *args, **kwargs): 
        super(ShdlcGetSensorTypeBase, self).__init__(
            0x24, *args, **kwargs
        )

class ShdlcGetSensorType(ShdlcGetSensorTypeBase):
    def __init__(self): 
        super(ShdlcGetSensorType, self).__init__(
            data=[], max_response_time=0.1,
            min_response_length=1, max_response_length=1
        )

    def interpret_response(self, data): 
        """
        Sensor Type: u8t[0…4]
        0: Flow Sensor (SF04 based products)
        1: Humidity Sensor (SHTxx products)
        2: Flow Sensor (SF05 based products)
        3: Flow Sensor (SF06 based products) (Firmware ≥1.7)
        4: not available
        :return int: Sensor type ID.
        """
        data_bytes = bytearray(data)  
        sensor_type = int(data_bytes[0])
        if sensor_type == 0: 
            return "Flow Sensor (SF04 based products)"
        elif sensor_type == 1: 
            return "Humidity Sensor (SHTxx products)"
        elif sensor_type == 2: 
            return "Flow Sensor (SF05 based products)"
        elif sensor_type == 3: 
            return "Flow Sensor (SF06 based products)"
        else: 
            return "Not available"


class ShdlcCmdGetI2cSlaveAddressBase(ShdlcCommand): 
    """
    SHDLC command ID: 0x25 "Get Sensor Address (I2C)".
    """

    def __init__(self, *args, **kwargs): 
        super(ShdlcCmdGetI2cSlaveAddressBase, self).__init__(
            0x25, *args, **kwargs
        )

class ShdlcCmdGetI2cSlaveAddress(ShdlcCmdGetI2cSlaveAddressBase):
    def __init__(self): 
        super(ShdlcCmdGetI2cSlaveAddress, self).__init__(
            data=[], max_response_time=0.1,
            min_response_length=1, max_response_length=2
        )

    def interpret_response(self, data): 
        """
        :return byte: I2C Slave Address.
        """
        data_bytes = bytearray(data)  
        i2c_addr = int(data_bytes[0])
        return i2c_addr
    

class ShdlcStartContinuousMeasurementBase(ShdlcCommand): 
    """
    SHDLC command ID: 0x33 "Start Continuous Measurement".
    """

    def __init__(self, *args, **kwargs): 
        super(ShdlcStartContinuousMeasurementBase, self).__init__(
            0x33, *args, **kwargs
        )

class ShdlcStartContinuousMeasurement(ShdlcStartContinuousMeasurementBase):
    """
    SHDLC command to start continuous measurement mode on the sensor.
    The measurement interval and medium type must be specified.

    [IMPORTANT] Make sure to stop the continuous measurement mode, otherwise you will 
                will not have access to other commands (EEPROM) until the device is reset.
    """

    _MEASUREMENT_INTERVAL_60000_MS = [0xEA, 0x60]  # 60 s
    _MEASUREMENT_INTERVAL_10000_MS = [0x27, 0x10]  # 10 s
    _MEASUREMENT_INTERVAL_1000_MS  = [0x03, 0xE8]  # 1 s
    _MEASUREMENT_INTERVAL_100_MS   = [0x00, 0x64]  # 100 ms 
    _MEASUREMENT_INTERVAL_50_MS    = [0x00, 0x32]  # 50 ms
    _MEASUREMENT_INTERVAL_20_MS    = [0x00, 0x14]  # 20 ms

    _I2C_MEAS_CMD_MEDIUM_WATER = [0x36, 0x08]    # Medium: H20 calibration
    _I2C_MEAS_CMD_MEDIUM_IPA   = [0x36, 0x15]    # Medium: Isopropyl Alcohol calibration

    def __init__(self, measurement_interval, i2c_medium_command): 
        """
        Constructor.

        :param bytes-like measurement_interval: Measurement interval in ms (2 bytes).
        :param bytes-like i2c_medium_command: I2C medium command (2 bytes).
        """
        data = bytearray()
        data.extend(measurement_interval)
        data.extend(i2c_medium_command)
        super(ShdlcStartContinuousMeasurement, self).__init__(
            data=data, max_response_time=0.1,
            min_response_length=0, max_response_length=0
        )
    
    def interpret_response(self, data): 
        """
        This command does not expect any response data.
        """
        if len(data) != 0:
            log.warning("Unexpected data received for StartContinuousMeasurement: %s", data)
        return None
    

class ShdlcGetContinuousMeasurementStatusBase(ShdlcCommand):
    """
    SHDLC command ID: 0x33 "Get Continuous Measurement Status".
    """

    def __init__(self, *args, **kwargs): 
        super(ShdlcGetContinuousMeasurementStatusBase, self).__init__(
            0x33, *args, **kwargs
        )

class ShdlcGetContinuousMeasurementStatus(ShdlcGetContinuousMeasurementStatusBase):
    def __init__(self): 
        super(ShdlcGetContinuousMeasurementStatus, self).__init__(
            data=[], max_response_time=0.1,
            min_response_length=1, max_response_length=2
        )

    def interpret_response(self, data): 
        """
        :return int: Measurement interval in ms.
        """
        data_bytes = bytearray(data)  
        data_bytes = (data_bytes[0] << 8) | data_bytes[1]
        measurment_interval = int(data_bytes)
        return measurment_interval
    

class ShdlcStopContinuousMeasurementBase(ShdlcCommand): 
    """
    SHDLC command ID: 0x34 "Stop Continuous Measurement".
    """

    def __init__(self, *args, **kwargs): 
        super(ShdlcStopContinuousMeasurementBase, self).__init__(
            0x34, *args, **kwargs
        )

class ShdlcStopContinuousMeasurement(ShdlcStopContinuousMeasurementBase):
    """
    SHDLC command to stop continuous measurement mode on the sensor.
    Stop command is provided via flow sensor I2C stop code found in the datasheet.

    [IMPORTANT] Make sure to stop the continuous measurement mode, otherwise you will 
                will not have access to other commands (EEPROM) until the device is reset.
    """

    _I2C_STOP_CODE = [0x3F, 0xF9]       # I2C Stop code

    def __init__(self, stop_code):
        """
        Constructor.

        :param bytes-like stop_code: I2C stop code (2 bytes).
        """
        data = bytearray()
        data.extend(stop_code)
        super(ShdlcStopContinuousMeasurement, self).__init__(
            data=data, max_response_time=0.1,
            min_response_length=0, max_response_length=0
        )
    
    def interpret_response(self, data):
        """
        This command does not expect any response data.
        """
        if len(data) != 0:
            log.warning("Unexpected data received for StopContinuousMeasurement: %s", data)
        return None
    

class ShdlcGetLastMeasurementBase(ShdlcCommand):
    """
    SHDLC command ID: 0x35 "Get Last Measurement".
    """

    def __init__(self, *args, **kwargs): 
        super(ShdlcGetLastMeasurementBase, self).__init__(
            0x35, *args, **kwargs
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


class ShdlcGetLastMeasurement(ShdlcGetLastMeasurementBase):
    """
    SHDLC command to get the last measurement data from the sensor.
    The requested signals must be specified.
    """

    _SIGNALS = [0x00, 0x03]            # Request all 3 signals

    def __init__(self, signals): 
        data = bytearray()
        data.extend(signals)
        super(ShdlcGetLastMeasurement, self).__init__(
            data=data, max_response_time=0.1,
            min_response_length=2, max_response_length=9
        )

    def interpret_response(self, data): 
        """
        :return int: Last measurement value as signed 32-bit integer.
        """
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
        
