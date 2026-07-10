from shdlc_command import ShdlcGetVersion, ShdlcDeviceSelfTest, \
    ShdlcCmdGetI2cSlaveAddress, ShdlcGetVoltage, ShdlcGetSensorType, \
    ShdlcStopContinuousMeasurement
from port import ShdlcSerialPort
from interface import ShdlcInterface


def main():

    port = "/dev/ttyUSB0"  
    baudrate = 115200
    with ShdlcSerialPort(port=port, baudrate=baudrate) as shdlc_port:
        interface = ShdlcInterface(port=shdlc_port)
        
        i2c_stop_cmd = ShdlcStopContinuousMeasurement(
            stop_code=ShdlcStopContinuousMeasurement._I2C_STOP_CODE
        )
        _, error_state = interface.execute(
            slave_address=0x00,
            command=i2c_stop_cmd
        )
        print(f"Stopped Continuous Measurement, Error state: {error_state}")

        get_version_cmd = ShdlcGetVersion()
        version_info, error_state = interface.execute(
            slave_address=0x00,
            command=get_version_cmd
        )

        print(f"Version Info: {version_info}, Error state: {error_state}")

        selftest_cmd = ShdlcDeviceSelfTest()
        selftest_result, error_state = interface.execute(
            slave_address=0x00,
            command=selftest_cmd
        )

        print(f"Self Test Result: {selftest_result}, Error state: {error_state}")

        get_sensor_voltage_cmd = ShdlcGetVoltage()
        sensor_voltage, error_state = interface.execute(
            slave_address=0x00,
            command=get_sensor_voltage_cmd
        )

        print(f"Sensor Voltage: {sensor_voltage} V, Error state: {error_state}")

        get_sensor_type_cmd = ShdlcGetSensorType()
        sensor_type, error_state = interface.execute(
            slave_address=0x00,
            command=get_sensor_type_cmd
        )
        print(f"Sensor Type: {sensor_type}, Error state: {error_state}")

        i2c_slave_address_cmd = ShdlcCmdGetI2cSlaveAddress()
        i2c_address, error_state = interface.execute(
            slave_address=0x00,
            command=i2c_slave_address_cmd
        )
        print(f"I2C Slave Address: {i2c_address:#04x}, Error state: {error_state}")


if __name__ == "__main__":
    main()
