import time
import serial
from datetime import datetime
debugLevel =12
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
                if(len(slaveTWCRoundRobin) > 0):
                    slaveTWC = slaveTWCRoundRobin[idxSlaveToSendNextHeartbeat]
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
                        delete_slave(slaveTWC.TWCID)
                    else:
                        slaveTWC.send_master_heartbeat()

                    idxSlaveToSendNextHeartbeat = idxSlaveToSendNextHeartbeat + 1
                    if(idxSlaveToSendNextHeartbeat >= len(slaveTWCRoundRobin)):
                        idxSlaveToSendNextHeartbeat = 0
                    time.sleep(0.1) # give slave time to respond