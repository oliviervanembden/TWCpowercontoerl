import time
import serial
from datetime import datetime
import re
debugLevel =12
slaveConnected = False
# All TWCs ship with a random two-byte TWCID. We default to using 0x7777 as our
# fake TWC ID. There is a 1 in 64535 chance that this ID will match each real
# TWC on the network, in which case you should pick a different random id below.
# This isn't really too important because even if this ID matches another TWC on
# the network, that TWC will pick its own new random ID as soon as it sees ours
# conflicts.
fakeTWCID = bytearray(b'\x77\x77')
# TWCs send a seemingly-random byte after their 2-byte TWC id in a number of
# messages. I call this byte their "Sign" for lack of a better term. The byte
# never changes unless the TWC is reset or power cycled. We use hard-coded
# values for now because I don't know if there are any rules to what values can
# be chosen. I picked 77 because it's easy to recognize when looking at logs.
# These shouldn't need to be changed.
masterSign = bytearray(b'\x77')
numInitMsgsToSend = 10
# TWC's rs485 port runs at 9600 baud which has been verified with an
# oscilloscope. Don't change this unless something changes in future hardware.
baud = 9600
# Most users will have only one ttyUSB adapter plugged in and the default value
# of '/dev/ttyUSB0' below will work. If not, run 'dmesg |grep ttyUSB' on the
# command line to find your rs485 adapter and put its ttyUSB# value in the
# parameter below.
# If you're using a non-USB adapter like an RS485 shield, the value may need to
# be something like '/dev/serial0'.
rs485Adapter = '/dev/ttyUSB0'

ser = serial.Serial(rs485Adapter, baud, timeout=0)

def hex_str(s:str):
    return " ".join("{:02X}".format(ord(c)) for c in s)

def time_now():
    return(datetime.now().strftime("%H:%M:%S"))

def send_master_linkready1():
    if(debugLevel >= 1):
        print(time_now() + ": Send master linkready1")

    # When master is powered on or reset, it sends 5 to 7 copies of this
    # linkready1 message followed by 5 copies of linkready2 (I've never seen
    # more or less than 5 of linkready2).
    #
    # This linkready1 message advertises master's TWCID to other slaves on the
    # network.
    # If a slave happens to have the same id as master, it will pick a new
    # random TWCID. Other than that, slaves don't seem to respond to linkready1.

    # linkready1 and linkready2 are identical except FC E1 is replaced by FB E2
    # in bytes 2-3. Both messages will cause a slave to pick a new id if the
    # slave's id conflicts with master.
    # If a slave stops sending heartbeats for awhile, master may send a series
    # of linkready1 and linkready2 messages in seemingly random order, which
    # means they don't indicate any sort of startup state.

    # linkready1 is not sent again after boot/reset unless a slave sends its
    # linkready message.
    # At that point, linkready1 message may start sending every 1-5 seconds, or
    # it may not be sent at all.
    # Behaviors I've seen:
    #   Not sent at all as long as slave keeps responding to heartbeat messages
    #   right from the start.
    #   If slave stops responding, then re-appears, linkready1 gets sent
    #   frequently.

    # One other possible purpose of linkready1 and/or linkready2 is to trigger
    # an error condition if two TWCs on the network transmit those messages.
    # That means two TWCs have rotary switches setting them to master mode and
    # they will both flash their red LED 4 times with top green light on if that
    # happens.

    # Also note that linkready1 starts with FC E1 which is similar to the FC D1
    # message that masters send out every 4 hours when idle. Oddly, the FC D1
    # message contains all zeros instead of the master's id, so it seems
    # pointless.

    # I also don't understand the purpose of having both linkready1 and
    # linkready2 since only two or more linkready2 will provoke a response from
    # a slave regardless of whether linkready1 was sent previously. Firmware
    # trace shows that slaves do something somewhat complex when they receive
    # linkready1 but I haven't been curious enough to try to understand what
    # they're doing. Tests show neither linkready1 or 2 are necessary. Slaves
    # send slave linkready every 10 seconds whether or not they got master
    # linkready1/2 and if a master sees slave linkready, it will start sending
    # the slave master heartbeat once per second and the two are then connected.
    send_msg(bytearray(b'\xFC\xE1') + fakeTWCID + masterSign + bytearray(b'\x00\x00\x00\x00\x00\x00\x00\x00'))

def unescape_msg(msg:bytearray, msgLen):
    # Given a message received on the RS485 network, remove leading and trailing
    # C0 byte, unescape special byte values, and verify its data matches the CRC
    # byte.
    msg = msg[0:msgLen]

    # See notes in send_msg() for the way certain bytes in messages are escaped.
    # We basically want to change db dc into c0 and db dd into db.
    # Only scan to one less than the length of the string to avoid running off
    # the end looking at i+1.
    i = 0
    while i < len(msg):
        if(msg[i] == 0xdb):
            if(msg[i+1] == 0xdc):
                # Replace characters at msg[i] and msg[i+1] with 0xc0,
                # shortening the string by one character. In Python, msg[x:y]
                # refers to a substring starting at x and ending immediately
                # before y. y - x is the length of the substring.
                msg[i:i+2] = [0xc0]
            elif(msg[i+1] == 0xdd):
                msg[i:i+2] = [0xdb]
            else:
                print(time_now(), "ERROR: Special character 0xDB in message is " \
                  "followed by invalid character 0x%02X.  " \
                  "Message may be corrupted." %
                  (msg[i+1]))

                # Replace the character with something even though it's probably
                # not the right thing.
                msg[i:i+2] = [0xdb]
        i = i+1

    # Remove leading and trailing C0 byte.
    msg = msg[1:len(msg)-1]
    return msg

def send_master_linkready2():
    if(debugLevel >= 1):
        print(time_now() + ": Send master linkready2")

    # This linkready2 message is also sent 5 times when master is booted/reset
    # and then not sent again if no other TWCs are heard from on the network.
    # If the master has ever seen a slave on the network, linkready2 is sent at
    # long intervals.
    # Slaves always ignore the first linkready2, but respond to the second
    # linkready2 around 0.2s later by sending five slave linkready messages.
    #
    # It may be that this linkready2 message that sends FB E2 and the master
    # heartbeat that sends fb e0 message are really the same, (same FB byte
    # which I think is message type) except the E0 version includes the TWC ID
    # of the slave the message is intended for whereas the E2 version has no
    # recipient TWC ID.
    #
    # Once a master starts sending heartbeat messages to a slave, it
    # no longer sends the global linkready2 message (or if it does,
    # they're quite rare so I haven't seen them).
    send_msg(bytearray(b'\xFB\xE2') + fakeTWCID + masterSign + bytearray(b'\x00\x00\x00\x00\x00\x00\x00\x00'))


def send_msg(msg):
    # Send msg on the RS485 network. We'll escape bytes with a special meaning,
    # add a CRC byte to the message end, and add a C0 byte to the start and end
    # to mark where it begins and ends.
    global ser, timeLastTx, fakeMaster, slaveTWCRoundRobin

    msg = bytearray(msg)
    checksum = 0
    for i in range(1, len(msg)):
        checksum += msg[i]

    msg.append(checksum & 0xFF)

    # Escaping special chars:
    # The protocol uses C0 to mark the start and end of the message.  If a C0
    # must appear within the message, it is 'escaped' by replacing it with
    # DB and DC bytes.
    # A DB byte in the message is escaped by replacing it with DB DD.
    #
    # User FuzzyLogic found that this method of escaping and marking the start
    # and end of messages is based on the SLIP protocol discussed here:
    #   https://en.wikipedia.org/wiki/Serial_Line_Internet_Protocol
    i = 0
    while(i < len(msg)):
        if(msg[i] == 0xc0):
            msg[i:i+1] = b'\xdb\xdc'
            i = i + 1
        elif(msg[i] == 0xdb):
            msg[i:i+1] = b'\xdb\xdd'
            i = i + 1
        i = i + 1

    msg = bytearray(b'\xc0' + msg + b'\xc0')

    if(debugLevel >= 9):
        print("Tx@" + time_now() + ": " + hex_str(msg))

    ser.write(msg)

    timeLastTx = time.time()

##############################
#
# Begin TWCSlave class
#

class TWCSlave:
    TWCID = None
    maxAmps = None

    # Protocol 2 TWCs tend to respond to commands sent using protocol 1, so
    # default to that till we know for sure we're talking to protocol 2.
    
    protocolVersion = 1
    minAmpsTWCSupports = 6
    masterHeartbeatData = bytearray(b'\x00\x00\x00\x00\x00\x00\x00\x00\x00')
    timeLastRx = time.time()


    # reported* vars below are reported to us in heartbeat messages from a Slave
    # TWC.
    reportedAmpsMax = 0
    reportedAmpsActual = 0
    reportedState = 0

    # reportedAmpsActual frequently changes by small amounts, like 5.14A may
    # frequently change to 5.23A and back.
    # reportedAmpsActualSignificantChangeMonitor is set to reportedAmpsActual
    # whenever reportedAmpsActual is at least 0.8A different than
    # reportedAmpsActualSignificantChangeMonitor. Whenever
    # reportedAmpsActualSignificantChangeMonitor is changed,
    # timeReportedAmpsActualChangedSignificantly is set to the time of the
    # change. The value of reportedAmpsActualSignificantChangeMonitor should not
    # be used for any other purpose. timeReportedAmpsActualChangedSignificantly
    # is used for things like preventing start and stop charge on a car more
    # than once per minute.
    reportedAmpsActualSignificantChangeMonitor = -1
    timeReportedAmpsActualChangedSignificantly = time.time()

    lastAmpsOffered = -1
    timeLastAmpsOfferedChanged = time.time()
    lastHeartbeatDebugOutput = ''
    timeLastHeartbeatDebugOutput = 0
    wiringMaxAmps = wiringMaxAmpsPerTWC

    def __init__(self, TWCID, maxAmps):
        self.TWCID = TWCID
        self.maxAmps = maxAmps
        self.unknownProtocolVersion = True

    def print_status(self, heartbeatData):
        global fakeMaster, masterTWCID

        try:
            debugOutput = ": SHB %02X%02X: %02X %05.2f/%05.2fA %02X%02X" % \
                (self.TWCID[0], self.TWCID[1], heartbeatData[0],
                (((heartbeatData[3] << 8) + heartbeatData[4]) / 100),
                (((heartbeatData[1] << 8) + heartbeatData[2]) / 100),
                heartbeatData[5], heartbeatData[6]
                )
            if(self.protocolVersion == 2):
                debugOutput += (" %02X%02X" % (heartbeatData[7], heartbeatData[8]))
            debugOutput += "  M"

            if(not fakeMaster):
                debugOutput += " %02X%02X" % (masterTWCID[0], masterTWCID[1])

            debugOutput += ": %02X %05.2f/%05.2fA %02X%02X" % \
                    (self.masterHeartbeatData[0],
                    (((self.masterHeartbeatData[3] << 8) + self.masterHeartbeatData[4]) / 100),
                    (((self.masterHeartbeatData[1] << 8) + self.masterHeartbeatData[2]) / 100),
                    self.masterHeartbeatData[5], self.masterHeartbeatData[6])
            if(self.protocolVersion == 2):
                debugOutput += (" %02X%02X" %
                    (self.masterHeartbeatData[7], self.masterHeartbeatData[8]))

            # Only output once-per-second heartbeat debug info when it's
            # different from the last output or if the only change has been amps
            # in use and it's only changed by 1.0 or less. Also output f it's
            # been 10 mins since the last output or if debugLevel is turned up
            # to 11.
            lastAmpsUsed = 0
            ampsUsed = 1
            debugOutputCompare = debugOutput
            m1 = re.search(r'SHB ....: .. (..\...)/', self.lastHeartbeatDebugOutput)
            if(m1):
                lastAmpsUsed = float(m1.group(1))
            m2 = re.search(r'SHB ....: .. (..\...)/', debugOutput)
            if(m2):
                ampsUsed = float(m2.group(1))
                if(m1):
                    debugOutputCompare = debugOutputCompare[0:m2.start(1)] + \
                        self.lastHeartbeatDebugOutput[m1.start(1):m1.end(1)] + \
                        debugOutputCompare[m2.end(1):]
            if(
                debugOutputCompare != self.lastHeartbeatDebugOutput
                or abs(ampsUsed - lastAmpsUsed) >= 1.0
                or time.time() - self.timeLastHeartbeatDebugOutput > 600
                or debugLevel >= 11
            ):
                print(time_now() + debugOutput)
                self.lastHeartbeatDebugOutput = debugOutput
                self.timeLastHeartbeatDebugOutput = time.time()
        except IndexError:
            # This happens if we try to access, say, heartbeatData[8] when
            # len(heartbeatData) < 9. This was happening due to a bug I fixed
            # but I may as well leave this here just in case.
            if(len(heartbeatData) != (7 if self.protocolVersion == 1 else 9)):
                print(time_now() + ': Error in print_status displaying heartbeatData',
                      heartbeatData, 'based on msg', hex_str(msg))
            if(len(self.masterHeartbeatData) != (7 if self.protocolVersion == 1 else 9)):
                print(time_now() + ': Error in print_status displaying masterHeartbeatData', self.masterHeartbeatData)

    def send_master_heartbeat(self):
        # Send our fake master's heartbeat to this TWCSlave.
        #
        # Heartbeat includes 7 bytes (Protocol 1) or 9 bytes (Protocol 2) of data
        # that we store in masterHeartbeatData.

        # Meaning of data:
        #
        # Byte 1 is a command:
        #   00 Make no changes
        #   02 Error
        #     Byte 2 appears to act as a bitmap where each set bit causes the
        #     slave TWC to enter a different error state. First 8 digits below
        #     show which bits are set and these values were tested on a Protocol
        #     2 TWC:
        #       0000 0001 = Middle LED blinks 3 times red, top LED solid green.
        #                   Manual says this code means 'Incorrect rotary switch
        #                   setting.'
        #       0000 0010 = Middle LED blinks 5 times red, top LED solid green.
        #                   Manual says this code means 'More than three Wall
        #                   Connectors are set to Slave.'
        #       0000 0100 = Middle LED blinks 6 times red, top LED solid green.
        #                   Manual says this code means 'The networked Wall
        #                   Connectors have different maximum current
        #                   capabilities.'
        #   	0000 1000 = No effect
        #   	0001 0000 = No effect
        #   	0010 0000 = No effect
        #   	0100 0000 = No effect
    	#       1000 0000 = No effect
        #     When two bits are set, the lowest bit (rightmost bit) seems to
        #     take precedence (ie 111 results in 3 blinks, 110 results in 5
        #     blinks).
        #
        #     If you send 02 to a slave TWC with an error code that triggers
        #     the middle LED to blink red, slave responds with 02 in its
        #     heartbeat, then stops sending heartbeat and refuses further
        #     communication. Slave's error state can be cleared by holding red
        #     reset button on its left side for about 4 seconds.
        #     If you send an error code with bitmap 11110xxx (where x is any bit),
        #     the error can not be cleared with a 4-second reset.  Instead, you
        #     must power cycle the TWC or 'reboot' reset which means holding
        #     reset for about 6 seconds till all the LEDs turn green.
        #   05 Tell slave charger to limit power to number of amps in bytes 2-3.
        #
        # Protocol 2 adds a few more command codes:
        #   06 Increase charge current by 2 amps.  Slave changes its heartbeat
        #      state to 06 in response. After 44 seconds, slave state changes to
        #      0A but amp value doesn't change.  This state seems to be used to
        #      safely creep up the amp value of a slave when the Master has extra
        #      power to distribute.  If a slave is attached to a car that doesn't
        #      want that many amps, Master will see the car isn't accepting the
        #      amps and stop offering more.  It's possible the 0A state change
        #      is not time based but rather indicates something like the car is
        #      now using as many amps as it's going to use.
        #   07 Lower charge current by 2 amps. Slave changes its heartbeat state
        #      to 07 in response. After 10 seconds, slave raises its amp setting
        #      back up by 2A and changes state to 0A.
        #      I could be wrong, but when a real car doesn't want the higher amp
        #      value, I think the TWC doesn't raise by 2A after 10 seconds. Real
        #      Master TWCs seem to send 07 state to all children periodically as
        #      if to check if they're willing to accept lower amp values. If
        #      they do, Master assigns those amps to a different slave using the
        #      06 state.
        #   08 Master acknowledges that slave stopped charging (I think), but
        #      the next two bytes contain an amp value the slave could be using.
        #   09 Tell slave charger to limit power to number of amps in bytes 2-3.
        #      This command replaces the 05 command in Protocol 1. However, 05
        #      continues to be used, but only to set an amp value to be used
        #      before a car starts charging. If 05 is sent after a car is
        #      already charging, it is ignored.
        #
        # Byte 2-3 is the max current a slave TWC can charge at in command codes
        # 05, 08, and 09. In command code 02, byte 2 is a bitmap. With other
        # command codes, bytes 2-3 are ignored.
        # If bytes 2-3 are an amp value of 0F A0, combine them as 0x0fa0 hex
        # which is 4000 in base 10. Move the decimal point two places left and
        # you get 40.00Amps max.
        #
        # Byte 4: 01 when a Master TWC is physically plugged in to a car.
        # Otherwise 00.
        #
        # Remaining bytes are always 00.
        #
        # Example 7-byte data that real masters have sent in Protocol 1:
        #   00 00 00 00 00 00 00  (Idle)
        #   02 04 00 00 00 00 00  (Error bitmap 04.  This happened when I
        #                         advertised a fake Master using an invalid max
        #                         amp value)
        #   05 0f a0 00 00 00 00  (Master telling slave to limit power to 0f a0
        #                         (40.00A))
        #   05 07 d0 01 00 00 00  (Master plugged in to a car and presumably
        #                          telling slaves to limit power to 07 d0
        #                          (20.00A). 01 byte indicates Master is plugged
        #                          in to a car.)
        global fakeTWCID, overrideMasterHeartbeatData, debugLevel, \
               timeLastTx, carApiVehicles

        if(len(overrideMasterHeartbeatData) >= 7):
            self.masterHeartbeatData = overrideMasterHeartbeatData

        if(self.protocolVersion == 2):
            # TODO: Start and stop charging using protocol 2 commands to TWC
            # instead of car api if I ever figure out how.
            if(self.lastAmpsOffered == 0 and self.reportedAmpsActual > 4.0):
                # Car is trying to charge, so stop it via car API.
                # car_api_charge() will prevent telling the car to start or stop
                # more than once per minute. Once the car gets the message to
                # stop, reportedAmpsActualSignificantChangeMonitor should drop
                # to near zero within a few seconds.
                # WARNING: If you own two vehicles and one is charging at home but
                # the other is charging away from home, this command will stop
                # them both from charging.  If the away vehicle is not currently
                # charging, I'm not sure if this would prevent it from charging
                # when next plugged in.
                queue_background_task({'cmd':'charge', 'charge':False})
            elif(self.lastAmpsOffered >= 5.0 and self.reportedAmpsActual < 2.0
                 and self.reportedState != 0x02
            ):
                # Car is not charging and is not reporting an error state, so
                # try starting charge via car api.
                queue_background_task({'cmd':'charge', 'charge':True})
            elif(self.reportedAmpsActual > 4.0):
                # At least one plugged in car is successfully charging. We don't
                # know which car it is, so we must set
                # vehicle.stopAskingToStartCharging = False on all vehicles such
                # that if any vehicle is not charging without us calling
                # car_api_charge(False), we'll try to start it charging again at
                # least once. This probably isn't necessary but might prevent
                # some unexpected case from never starting a charge. It also
                # seems less confusing to see in the output that we always try
                # to start API charging after the car stops taking a charge.
                for vehicle in carApiVehicles:
                    vehicle.stopAskingToStartCharging = False

        send_msg(bytearray(b'\xFB\xE0') + fakeTWCID + bytearray(self.TWCID)
                 + bytearray(self.masterHeartbeatData))


    def receive_slave_heartbeat(self, heartbeatData):
        # Handle heartbeat message received from real slave TWC.
        global debugLevel, nonScheduledAmpsMax, \
               maxAmpsToDivideAmongSlaves, wiringMaxAmpsAllTWCs, \
               timeLastGreenEnergyCheck, greenEnergyAmpsOffset, \
               slaveTWCRoundRobin, spikeAmpsToCancel6ALimit, \
               chargeNowAmps, chargeNowTimeEnd, minAmpsPerTWC

        now = time.time()
        self.timeLastRx = now

        self.reportedAmpsMax = ((heartbeatData[1] << 8) + heartbeatData[2]) / 100
        self.reportedAmpsActual = ((heartbeatData[3] << 8) + heartbeatData[4]) / 100
        self.reportedState = heartbeatData[0]

        # self.lastAmpsOffered is initialized to -1.
        # If we find it at that value, set it to the current value reported by the
        # TWC.
        if(self.lastAmpsOffered < 0):
            self.lastAmpsOffered = self.reportedAmpsMax

        # Keep track of the amps the slave is actually using and the last time it
        # changed by more than 0.8A.
        # Also update self.reportedAmpsActualSignificantChangeMonitor if it's
        # still set to its initial value of -1.
        if(self.reportedAmpsActualSignificantChangeMonitor < 0
           or abs(self.reportedAmpsActual - self.reportedAmpsActualSignificantChangeMonitor) > 0.8
        ):
            self.timeReportedAmpsActualChangedSignificantly = now
            self.reportedAmpsActualSignificantChangeMonitor = self.reportedAmpsActual

        ltNow = time.localtime()
        hourNow = ltNow.tm_hour + (ltNow.tm_min / 60)


    def set_last_amps_offered(self, desiredAmpsOffered):
        # self.lastAmpsOffered should only be changed using this sub.
        global debugLevel

        if(debugLevel >= 10):
            print("set_last_amps_offered(TWCID=" + hex_str(self.TWCID) +
                  ", desiredAmpsOffered=" + str(desiredAmpsOffered) + ")")

        if(desiredAmpsOffered != self.lastAmpsOffered):
            oldLastAmpsOffered = self.lastAmpsOffered
            self.lastAmpsOffered = desiredAmpsOffered

            # Set totalAmpsAllTWCs to the total amps all TWCs are actually using
            # minus amps this TWC is using, plus amps this TWC wants to use.
            totalAmpsAllTWCs = total_amps_actual_all_twcs() \
                  - self.reportedAmpsActual + self.lastAmpsOffered
            if(totalAmpsAllTWCs > wiringMaxAmpsAllTWCs):
                # totalAmpsAllTWCs would exceed wiringMaxAmpsAllTWCs if we
                # allowed this TWC to use desiredAmpsOffered.  Instead, try
                # offering as many amps as will increase total_amps_actual_all_twcs()
                # up to wiringMaxAmpsAllTWCs.
                self.lastAmpsOffered = int(wiringMaxAmpsAllTWCs -
                                          (total_amps_actual_all_twcs() - self.reportedAmpsActual))

                if(self.lastAmpsOffered < self.minAmpsTWCSupports):
                    # Always offer at least minAmpsTWCSupports amps.
                    # See notes in receive_slave_heartbeat() beneath
                    # 'if(maxAmpsToDivideAmongSlaves / numCarsCharging > minAmpsToOffer):'
                    self.lastAmpsOffered = self.minAmpsTWCSupports

                print("WARNING: Offering slave TWC %02X%02X %.1fA instead of " \
                    "%.1fA to avoid overloading wiring shared by all TWCs." % (
                    self.TWCID[0], self.TWCID[1], self.lastAmpsOffered, desiredAmpsOffered))

            if(self.lastAmpsOffered > self.wiringMaxAmps):
                # We reach this case frequently in some configurations, such as
                # when two 80A TWCs share a 125A line.  Therefore, don't print
                # an error.
                self.lastAmpsOffered = self.wiringMaxAmps
                if(debugLevel >= 10):
                    print("Offering slave TWC %02X%02X %.1fA instead of " \
                        "%.1fA to avoid overloading the TWC rated at %.1fA." % (
                        self.TWCID[0], self.TWCID[1], self.lastAmpsOffered,
                        desiredAmpsOffered, self.wiringMaxAmps))

            if(self.lastAmpsOffered != oldLastAmpsOffered):
                self.timeLastAmpsOfferedChanged = time.time()
        return self.lastAmpsOffered


#hier start de conicatie met de TWC
while True:
    try:
        # In this area, we always send a linkready message when we first start.
        # Whenever there is no data available from other TWCs to respond to,
        # we'll loop back to this point to send another linkready or heartbeat
        # message. By only sending our periodic messages when no incoming
        # message data is available, we reduce the chance that we will start
        # transmitting a message in the middle of an incoming message, which
        # would corrupt both messages.

        # Add a 25ms sleep to prevent pegging pi's CPU at 100%. Lower CPU means
        # less power used and less waste heat.
        time.sleep(0.025)

        now = time.time()
        # A real master sends 5 copies of linkready1 and linkready2 whenever
        # it starts up, which we do here.
        # It doesn't seem to matter if we send these once per second or once
        # per 100ms so I do once per 100ms to get them over with.
        if(numInitMsgsToSend > 5):
            send_master_linkready1()
            time.sleep(0.1) # give slave time to respond
            numInitMsgsToSend -= 1
        elif(numInitMsgsToSend > 0):
            send_master_linkready2()
            time.sleep(0.1) # give slave time to respond
            numInitMsgsToSend -= 1
        else:
            # After finishing the 5 startup linkready1 and linkready2
            # messages, master will send a heartbeat message to every slave
            # it's received a linkready message from. Do that here.
            # A real master would keep sending linkready messages periodically
            # as long as no slave was connected, but since real slaves send
            # linkready once every 10 seconds till they're connected to a
            # master, we'll just wait for that.
            if(time.time() - timeLastTx >= 1.0):
                # It's been about a second since our last heartbeat.
                if(slaveConnected):
                    #slaveTWC is the instans of Class TWCSlave
                    if(time.time() - slaveTWC.timeLastRx > 26):
                        # A real master stops sending heartbeats to a slave
                        # that hasn't responded for ~26 seconds. It may
                        # still send the slave a heartbeat every once in
                        # awhile but we're just going to scratch the slave
                        # from our little black book and add them again if
                        # they ever send us a linkready.
                        print(time_now() + ": WARNING: We haven't heard from slave " \
                            "%02X%02X for over 26 seconds.  " \
                            "Stop sending them heartbeat messages." % \
                            (slaveTWC.TWCID[0], slaveTWC.TWCID[1]))
                        slaveConnected = False
                    else:
                        slaveTWC.send_master_heartbeat()

                    time.sleep(0.1) # give slave time to respond


        ########################################################################
        # See if there's an incoming message on the RS485 interface.

        timeMsgRxStart = time.time()
        while True: #read message while true returns msg for data en mgLen for msg lengt
            now = time.time()
            dataLen = ser.inWaiting() #might need to remove () at the and 
            if(dataLen == 0):
                if(msgLen == 0):
                    # No message data waiting and we haven't received the
                    # start of a new message yet. Break out of inner while
                    # to continue at top of outer while loop where we may
                    # decide to send a periodic message.
                    break
                else:
                    # No message data waiting but we've received a partial
                    # message that we should wait to finish receiving.
                    if(now - timeMsgRxStart >= 2.0):
                        if(debugLevel >= 9):
                            print(time_now() + ": Msg timeout (" + hex_str(ignoredData) +
                                  ') ' + hex_str(msg[0:msgLen]))
                        msgLen = 0
                        ignoredData = bytearray()
                        break

                    time.sleep(0.025)
                    continue
            else:
                dataLen = 1
                data = ser.read(dataLen)

            if(dataLen != 1):
                # This should never happen
                print("WARNING: No data available.")
                break

            timeMsgRxStart = now
            timeLastRx = now
            if(msgLen == 0 and data[0] != 0xc0):
                # We expect to find these non-c0 bytes between messages, so
                # we don't print any warning at standard debug levels.
                if(debugLevel >= 11):
                    print("Ignoring byte %02X between messages." % (data[0]))
                ignoredData += data
                continue
            elif(msgLen > 0 and msgLen < 15 and data[0] == 0xc0):
                # If you see this when the program is first started, it
                # means we started listening in the middle of the TWC
                # sending a message so we didn't see the whole message and
                # must discard it. That's unavoidable.
                # If you see this any other time, it means there was some
                # corruption in what we received. It's normal for that to
                # happen every once in awhile but there may be a problem
                # such as incorrect termination or bias resistors on the
                # rs485 wiring if you see it frequently.
                if(debugLevel >= 10):
                    print("Found end of message before full-length message received.  " \
                          "Discard and wait for new message.")

                msg = data
                msgLen = 1
                continue

            if(msgLen == 0):
                msg = bytearray()
            msg += data
            msgLen += 1

            # Messages are usually 17 bytes or longer and end with \xc0\xfe.
            # However, when the network lacks termination and bias
            # resistors, the last byte (\xfe) may be corrupted or even
            # missing, and you may receive additional garbage bytes between
            # messages.
            #
            # TWCs seem to account for corruption at the end and between
            # messages by simply ignoring anything after the final \xc0 in a
            # message, so we use the same tactic. If c0 happens to be within
            # the corrupt noise between messages, we ignore it by starting a
            # new message whenever we see a c0 before 15 or more bytes are
            # received.
            #
            # Uncorrupted messages can be over 17 bytes long when special
            # values are "escaped" as two bytes. See notes in send_msg.
            #
            # To prevent most noise between messages, add a 120ohm
            # "termination" resistor in parallel to the D+ and D- lines.
            # Also add a 680ohm "bias" resistor between the D+ line and +5V
            # and a second 680ohm "bias" resistor between the D- line and
            # ground. See here for more information:
            #   https://www.ni.com/support/serial/resinfo.htm
            #   http://www.ti.com/lit/an/slyt514/slyt514.pdf
            # This explains what happens without "termination" resistors:
            #   https://e2e.ti.com/blogs_/b/analogwire/archive/2016/07/28/rs-485-basics-when-termination-is-necessary-and-how-to-do-it-properly
            if(msgLen >= 16 and data[0] == 0xc0):
                break

        if(msgLen >= 16):
            msg = unescape_msg(msg, msgLen)
            # Set msgLen = 0 at start so we don't have to do it on errors below.
            # len($msg) now contains the unescaped message length.
            msgLen = 0

            msgRxCount += 1

            # When the sendTWCMsg web command is used to send a message to the
            # TWC, it sets lastTWCResponseMsg = b''.  When we see that here,
            # set lastTWCResponseMsg to any unusual message received in response
            # to the sent message.  Never set lastTWCResponseMsg to a commonly
            # repeated message like master or slave linkready, heartbeat, or
            # voltage/kWh report.
            if(lastTWCResponseMsg == b''
               and msg[0:2] != b'\xFB\xE0' and msg[0:2] != b'\xFD\xE0'
               and msg[0:2] != b'\xFC\xE1' and msg[0:2] != b'\xFB\xE2'
               and msg[0:2] != b'\xFD\xE2' and msg[0:2] != b'\xFB\xEB'
               and msg[0:2] != b'\xFD\xEB' and msg[0:2] != b'\xFD\xE0'
            ):
                lastTWCResponseMsg = msg

            if(debugLevel >= 9):
                print("Rx@" + time_now() + ": (" + hex_str(ignoredData) + ') ' \
                      + hex_str(msg) + "")

            ignoredData = bytearray()

            # After unescaping special values and removing the leading and
            # trailing C0 bytes, the messages we know about are always 14 bytes
            # long in original TWCs, or 16 bytes in newer TWCs (protocolVersion
            # == 2).
            if(len(msg) != 14 and len(msg) != 16 and len(msg) != 20):
                # In firmware 4.5.3, FD EB (kWh and voltage report), FD ED, FD
                # EE, FD EF, FD F1, and FB A4 messages are length 20 while most
                # other messages are length 16. I'm not sure if there are any
                # length 14 messages remaining.
                print(time_now() + ": ERROR: Ignoring message of unexpected length %d: %s" % \
                       (len(msg), hex_str(msg)))
                continue

            checksumExpected = msg[len(msg) - 1]
            checksum = 0
            for i in range(1, len(msg) - 1):
                checksum += msg[i]

            if((checksum & 0xFF) != checksumExpected):
                print("ERROR: Checksum %X does not match %02X.  Ignoring message: %s" %
                    (checksum, checksumExpected, hex_str(msg)))
                continue


            ############################
            # Pretend to be a master TWC

            foundMsgMatch = False
            # We end each regex message search below with \Z instead of $
            # because $ will match a newline at the end of the string or the
            # end of the string (even without the re.MULTILINE option), and
            # sometimes our strings do end with a newline character that is
            # actually the CRC byte with a value of 0A or 0D.
            msgMatch = re.search(b'^\xfd\xe2(..)(.)(..)\x00\x00\x00\x00\x00\x00.+\Z', msg, re.DOTALL)
            if(msgMatch and foundMsgMatch == False):
                # Handle linkready message from slave.
                #
                # We expect to see one of these before we start sending our
                # own heartbeat message to slave.
                # Once we start sending our heartbeat to slave once per
                # second, it should no longer send these linkready messages.
                # If slave doesn't hear master's heartbeat for around 10
                # seconds, it sends linkready once per 10 seconds and starts
                # flashing its red LED 4 times with the top green light on.
                # Red LED stops flashing if we start sending heartbeat
                # again.
                foundMsgMatch = True
                senderID = msgMatch.group(1)
                sign = msgMatch.group(2)
                maxAmps = ((msgMatch.group(3)[0] << 8) + msgMatch.group(3)[1]) / 100

                if(debugLevel >= 1):
                    print(time_now() + ": %.2f amp slave TWC %02X%02X is ready to link.  Sign: %s" % \
                        (maxAmps, senderID[0], senderID[1],
                        hex_str(sign)))


                spikeAmpsToCancel6ALimit = 16

                if(senderID == fakeTWCID):
                    print(time_now + ": Slave TWC %02X%02X reports same TWCID as master.  " \
                            "Slave should resolve by changing its TWCID." % \
                            (senderID[0], senderID[1]))
                    # I tested sending a linkready to a real master with the
                    # same TWCID as master and instead of master sending back
                    # its heartbeat message, it sent 5 copies of its
                    # linkready1 and linkready2 messages. Those messages
                    # will prompt a real slave to pick a new random value
                    # for its TWCID.
                    #
                    # We mimic that behavior by setting numInitMsgsToSend =
                    # 10 to make the idle code at the top of the for()
                    # loop send 5 copies of linkready1 and linkready2.
                    numInitMsgsToSend = 10
                    continue

                # We should always get this linkready message at least once
                # and generally no more than once, so this is a good
                # opportunity to add the slave to our known pool of slave
                # devices.
                slaveTWC =  TWCSlave(senderID, maxAmps)

                if(slaveTWC.unknownProtocolVersion):
                    if(len(msg) == 14):
                        slaveTWC.protocolVersion = 1
                        slaveTWC.minAmpsTWCSupports = 5
                    elif(len(msg) == 16):
                        slaveTWC.protocolVersion = 2
                        slaveTWC.minAmpsTWCSupports = 6

                    if(debugLevel >= 1):
                        print(time_now() + ": Set slave TWC %02X%02X protocolVersion to %d, minAmpsTWCSupports to %d." % \
                                (senderID[0], senderID[1], slaveTWC.protocolVersion, slaveTWC.minAmpsTWCSupports))

                # We expect maxAmps to be 80 on U.S. chargers and 32 on EU
                # chargers. Either way, don't allow
                # slaveTWC.wiringMaxAmps to be greater than maxAmps.
                if(slaveTWC.wiringMaxAmps > maxAmps):
                    print("\n\n!!! DANGER DANGER !!!\nYou have set wiringMaxAmpsPerTWC to "
                            + str(wiringMaxAmpsPerTWC)
                            + " which is greater than the max "
                            + str(maxAmps) + " amps your charger says it can handle.  " \
                            "Please review instructions in the source code and consult an " \
                            "electrician if you don't know what to do.")
                    slaveTWC.wiringMaxAmps = maxAmps / 4

                # Make sure we print one SHB message after a slave
                # linkready message is received by clearing
                # lastHeartbeatDebugOutput. This helps with debugging
                # cases where I can't tell if we responded with a
                # heartbeat or not.
                slaveTWC.lastHeartbeatDebugOutput = ''

                slaveTWC.timeLastRx = time.time()
                slaveTWC.send_master_heartbeat()
            else:
                msgMatch = re.search(b'\A\xfd\xe0(..)(..)(.......+?).\Z', msg, re.DOTALL)
            if(msgMatch and foundMsgMatch == False):
                # Handle heartbeat message from slave.
                #
                # These messages come in as a direct response to each
                # heartbeat message from master. Slave does not send its
                # heartbeat until it gets one from master first.
                # A real master sends heartbeat to a slave around once per
                # second, so we do the same near the top of this for()
                # loop. Thus, we should receive a heartbeat reply from the
                # slave around once per second as well.
                foundMsgMatch = True
                senderID = msgMatch.group(1)
                receiverID = msgMatch.group(2)
                heartbeatData = msgMatch.group(3)

                try:
                    slaveTWC = slaveTWCs[senderID]
                except KeyError:
                    # Normally, a slave only sends us a heartbeat message if
                    # we send them ours first, so it's not expected we would
                    # hear heartbeat from a slave that's not in our list.
                    print(time_now() + ": ERROR: Received heartbeat message from " \
                            "slave %02X%02X that we've not met before." % \
                            (senderID[0], senderID[1]))
                    continue

                if(fakeTWCID == receiverID):
                    slaveTWC.receive_slave_heartbeat(heartbeatData)
                else:
                    # I've tried different fakeTWCID values to verify a
                    # slave will send our fakeTWCID back to us as
                    # receiverID. However, I once saw it send receiverID =
                    # 0000.
                    # I'm not sure why it sent 0000 and it only happened
                    # once so far, so it could have been corruption in the
                    # data or an unusual case.
                    if(debugLevel >= 1):
                        print(time_now() + ": WARNING: Slave TWC %02X%02X status data: " \
                                "%s sent to unknown TWC %02X%02X." % \
                            (senderID[0], senderID[1],
                            hex_str(heartbeatData), receiverID[0], receiverID[1]))
            else:
                msgMatch = re.search(b'\A\xfd\xeb(..)(..)(.+?).\Z', msg, re.DOTALL)
            if(msgMatch and foundMsgMatch == False):
                # Handle kWh total and voltage message from slave.
                #
                # This message can only be generated by TWCs running newer
                # firmware.  I believe it's only sent as a response to a
                # message from Master in this format:
                #   FB EB <Master TWCID> <Slave TWCID> 00 00 00 00 00 00 00 00 00
                # Since we never send such a message, I don't expect a slave
                # to ever send this message to us, but we handle it just in
                # case.
                # According to FuzzyLogic, this message has the following
                # format on an EU (3-phase) TWC:
                #   FD EB <Slave TWCID> 00000038 00E6 00F1 00E8 00
                #   00000038 (56) is the total kWh delivered to cars
                #     by this TWC since its construction.
                #   00E6 (230) is voltage on phase A
                #   00F1 (241) is voltage on phase B
                #   00E8 (232) is voltage on phase C
                #
                # I'm guessing in world regions with two-phase power that
                # this message would be four bytes shorter, but the pattern
                # above will match a message of any length that starts with
                # FD EB.
                foundMsgMatch = True
                senderID = msgMatch.group(1)
                receiverID = msgMatch.group(2)
                data = msgMatch.group(3)

                if(debugLevel >= 1):
                    print(time_now() + ": Slave TWC %02X%02X unexpectedly reported kWh and voltage data: %s." % \
                        (senderID[0], senderID[1],
                        hex_str(data)))
            else:
                msgMatch = re.search(b'\A\xfc(\xe1|\xe2)(..)(.)\x00\x00\x00\x00\x00\x00\x00\x00.+\Z', msg, re.DOTALL)
            if(msgMatch and foundMsgMatch == False):
                foundMsgMatch = True
                print(time_now() + " ERROR: TWC is set to Master mode so it can't be controlled by TWCManager.  " \
                        "Search installation instruction PDF for 'rotary switch' and set " \
                        "switch so its arrow points to F on the dial.")
            if(foundMsgMatch == False):
                print(time_now() + ": *** UNKNOWN MESSAGE FROM SLAVE:" + hex_str(msg)
                        + "\nPlease private message user CDragon at http://teslamotorsclub.com " \
                        "with a copy of this error.")
