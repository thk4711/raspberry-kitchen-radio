#!/usr/bin/python
import re
# Use smbus2 rather than the legacy `smbus` binding. smbus2 is a pure-Python,
# API-compatible drop-in (same write_byte_data / read_i2c_block_data / ... calls
# and SMBus(busnum) constructor) and is the module actually shipped in the
# Buildroot appliance image (BR2_PACKAGE_PYTHON_SMBUS2). The old `smbus` module
# (from i2c-tools' python bindings) is NOT installed on the image, so importing
# it here would crash the ADC controller — and, since it is constructed during
# RadioController startup, the whole app.
import smbus2 as smbus
import logging

# ===========================================================================
# Adafruit_I2C Class
# ===========================================================================


logger = logging.getLogger(__name__)

class Adafruit_I2C(object):

  def __init__(self, address, busnum=1, debug=False):
    self.address = address
    self.bus = smbus.SMBus(busnum)
    logger.debug(f"I2C: init bus {busnum}; address {address}")


  def reverseByteOrder(self, data):
    "Reverses the byte order of an int (16-bit) or long (32-bit) value"
    # Courtesy Vishal Sapre
    byteCount = len(hex(data)[2:].replace('L','')[::2])
    val       = 0
    for i in range(byteCount):
      val    = (val << 8) | (data & 0xff)
      data >>= 8
    return val

  def errMsg(self):
    logger.error(f"Error accessing 0x{self.address:02X} : Check your I2C address" )
    return -1

  def write8(self, reg, value):
    "Writes an 8-bit value to the specified register/address"
    try:
      self.bus.write_byte_data(self.address, reg, value)
      logger.debug(f"I2C: Wrote 0x{value:02X} to register 0x{reg:02X}")
    except IOError as err:
      return self.errMsg()

  def write16(self, reg, value):
    "Writes a 16-bit value to the specified register/address pair"
    try:
      self.bus.write_word_data(self.address, reg, value)
      logger.debug(f"I2C: Wrote 0x{value:02X} to register pair 0x{reg:02X} 0x{reg+1:02X}")
    except IOError as err:
      return self.errMsg()

  def writeRaw8(self, value):
    "Writes an 8-bit value on the bus"
    try:
      self.bus.write_byte(self.address, value)
      logger.debug(f"I2C: Wrote 0x{value:02X}")
    except IOError as err:
      return self.errMsg()

  def writeList(self, reg, list):
    "Writes an array of bytes using I2C format"
    try:
      logger.debug (f"I2C: Writing list to register 0x{reg:02X}")
      logger.debug (list)
      self.bus.write_i2c_block_data(self.address, reg, list)
    except IOError as err:
      return self.errMsg()

  def readList(self, reg, length):
    "Read a list of bytes from the I2C device"
    try:
      results = self.bus.read_i2c_block_data(self.address, reg, length)
      logger.debug (f"I2C: Device 0x{self.address:02X} returned the following from reg 0x{reg:02X}" )
      logger.debug (results)
      return results
    except IOError as err:
      return self.errMsg()

  def readU8(self, reg):
    "Read an unsigned byte from the I2C device"
    try:
      result = self.bus.read_byte_data(self.address, reg)
      logger.debug (f"I2C: Device 0x{self.address:02X} returned 0x{result & 0xFF:02X} from reg 0x{reg:02X}" )
      return result
    except IOError as err:
      return self.errMsg()

  def readS8(self, reg):
    "Reads a signed byte from the I2C device"
    try:
      result = self.bus.read_byte_data(self.address, reg)
      if result > 127: 
        result -= 256
      logger.debug (f"I2C: Device 0x{self.address:02X} returned 0x{result & 0xFF:02X} from reg 0x{reg:02X}" )
      return result
    except IOError as err:
      return self.errMsg()

  def readU16(self, reg, little_endian=True):
    "Reads an unsigned 16-bit value from the I2C device"
    try:
      result = self.bus.read_word_data(self.address,reg)
      # Swap bytes if using big endian because read_word_data assumes little 
      # endian on ARM (little endian) systems.
      if not little_endian:
        result = ((result << 8) & 0xFF00) + (result >> 8)
      logger.debug (f"I2C: Device 0x{self.address:02X} returned 0x{result & 0xFFFF:02X} from reg 0x{reg:02X}" )    
      return result
    except IOError as err:
      return self.errMsg()

  def readS16(self, reg, little_endian=True):
    "Reads a signed 16-bit value from the I2C device"
    try:
      result = self.readU16(reg,little_endian)
      if result > 32767: result -= 65536
      return result
    except IOError as err:
      return self.errMsg()

if __name__ == '__main__':
  try:
    bus = Adafruit_I2C(address=0)
    print ("Default I2C bus is accessible")
  except:
    print ("Error accessing default I2C bus")
