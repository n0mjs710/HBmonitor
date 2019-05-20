#!/usr/bin/env python
#
###############################################################################
#   Copyright (C) 2016-2019  Cortney T. Buffington, N0MJS <n0mjs@me.com>
#
#   This program is free software; you can redistribute it and/or modify
#   it under the terms of the GNU General Public License as published by
#   the Free Software Foundation; either version 3 of the License, or
#   (at your option) any later version.
#
#   This program is distributed in the hope that it will be useful,
#   but WITHOUT ANY WARRANTY; without even the implied warranty of
#   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#   GNU General Public License for more details.
#
#   You should have received a copy of the GNU General Public License
#   along with this program; if not, write to the Free Software Foundation,
#   Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301  USA
###############################################################################

from __future__ import print_function

# Standard modules
import logging
import sys

# Twisted modules
from twisted.internet.protocol import ReconnectingClientFactory, Protocol
from twisted.protocols.basic import NetstringReceiver
from twisted.internet import reactor, task
from twisted.web.server import Site
#from twisted.web.static import File
from twisted.web.resource import Resource

# Autobahn provides websocket service under Twisted
from autobahn.twisted.websocket import WebSocketServerProtocol, WebSocketServerFactory

# Specific functions to import from standard modules
#from pprint import pprint
from time import time, strftime, localtime
from cPickle import loads
from binascii import b2a_hex as h
from os.path import getmtime
from collections import deque
from time import time

# Web templating environment
from jinja2 import Environment, PackageLoader, select_autoescape

# Utilities from K0USY Group sister project
from dmr_utils.utils import int_id, get_alias, try_download, mk_full_id_dict, hex_str_4

# Configuration variables and constants
from config import *

# Opcodes for reporting protocol to HBlink
OPCODE = {
    'CONFIG_REQ': '\x00',
    'CONFIG_SND': '\x01',
    'BRIDGE_REQ': '\x02',
    'BRIDGE_SND': '\x03',
    'CONFIG_UPD': '\x04',
    'BRIDGE_UPD': '\x05',
    'LINK_EVENT': '\x06',
    'BRDG_EVENT': '\x07',
    }

# Global Variables:
CONFIG      = {}
CTABLE      = {'MASTERS': {}, 'PEERS': {}, 'OPENBRIDGES': {}}
BRIDGES     = {}
BTABLE      = {}
BTABLE['BRIDGES'] = {}
BRIDGES_RX  = ''
CONFIG_RX   = ''
LOGBUF      = deque(100*[''], 100)
RED         = 'ff0000'
BLACK       = '000000'
GREEN       = '00ff00'
BLUE        = '0000ff'
ORANGE      = 'ff8000'
WHITE       = 'ffffff'


# For importing HTML templates
def get_template(_file):
    with open(_file, 'r') as html:
        return html.read()

# Alias string processor
def alias_string(_id, _dict):
    alias = get_alias(_id, _dict, 'CALLSIGN', 'CITY', 'STATE')
    if type(alias) == list:
        for x,item in enumerate(alias):
            if item == None:
                alias.pop(x)
        return ', '.join(alias)
    else:
        return alias

def alias_short(_id, _dict):
    alias = get_alias(_id, _dict, 'CALLSIGN', 'NAME')
    if type(alias) == list:
        for x,item in enumerate(alias):
            if item == None:
                alias.pop(x)
        return ', '.join(alias)
    else:
        return str(alias)

def alias_call(_id, _dict):
    alias = get_alias(_id, _dict, 'CALLSIGN')
    if type(alias) == list:
        for x,item in enumerate(alias):
            if item == None:
                alias.pop(x)
        return ', '.join(alias)
    else:
        return str(alias)
def alias_tgid(_id, _dict):
    alias = get_alias(_id, _dict, 'NAME')
    if type(alias) == list:
        return str(alias[0])
    else:
	    return str(alias)

# Return friendly elpasted time from time in seconds.
def since(_time):
    now = int(time())
    _time = now - int(_time)
    seconds = _time % 60
    minutes = (_time/60) % 60
    hours = (_time/60/60) % 24
    days = (_time/60/60/24) 
    if days:
        return '{}d {}h'.format(days, hours)
    elif hours:
        return '{}h {}m'.format(hours, minutes)
    elif minutes:
        return '{}m {}s'.format(minutes, seconds)
    else:
        return '{}s'.format(seconds)


def add_hb_peer(_peer_conf, _ctable_loc, _peer):
    _ctable_loc[int_id(_peer)] = {}
    _ctable_peer = _ctable_loc[int_id(_peer)]

    # if the Frequency is 000.xxx assume it's not an RF peer, otherwise format the text fields
    if _peer_conf['TX_FREQ'][:3] == '000' or _peer_conf['RX_FREQ'][:3] == '000':
        _ctable_peer['TX_FREQ'] = 'N/A'
        _ctable_peer['RX_FREQ'] = ''
    else:
        _ctable_peer['TX_FREQ'] = 'TX: ' + _peer_conf['TX_FREQ'][:3] + '.' + _peer_conf['TX_FREQ'][3:7]
        _ctable_peer['RX_FREQ'] = 'RX: ' + _peer_conf['RX_FREQ'][:3] + '.' + _peer_conf['RX_FREQ'][3:7]

    # timeslots are kinda complicated too. 0 = none, 1 or 2 mean that one slot, 3 is both, and anythign else it considered DMO
    if (_peer_conf['SLOTS'] == '0'):
        _ctable_peer['SLOTS'] = 'NONE'
    elif (_peer_conf['SLOTS'] <= '2'):
        _ctable_peer['SLOTS'] = _peer_conf['SLOTS']
    elif (_peer_conf['SLOTS'] == '3'):
        _ctable_peer['SLOTS'] = 'BOTH'
    else:
        _ctable_peer['SLOTS'] = 'DMO'

    # Simple translation items
    _ctable_peer['COLORCODE'] = _peer_conf['COLORCODE']
    _ctable_peer['CALLSIGN'] = _peer_conf['CALLSIGN']
    _ctable_peer['LOCATION'] = _peer_conf['LOCATION']
    _ctable_peer['CONNECTION'] = _peer_conf['CONNECTION']
    _ctable_peer['CONNECTED'] = since(_peer_conf['CONNECTED'])
    _ctable_peer['IP'] = _peer_conf['IP']
    _ctable_peer['PORT'] = _peer_conf['PORT']
    #_ctable_peer['LAST_PING'] = _peer_conf['LAST_PING']

    # SLOT 1&2 - for real-time montior: make the structure for later use
    for ts in range(1,3):
        _ctable_peer[ts]= {}
        _ctable_peer[ts]['COLOR'] = ''
        _ctable_peer[ts]['BGCOLOR'] = ''
        _ctable_peer[ts]['TS'] = ''
        _ctable_peer[ts]['TYPE'] = ''
        _ctable_peer[ts]['SUB'] = ''
        _ctable_peer[ts]['SRC'] = ''
        _ctable_peer[ts]['DEST'] = ''


# Build the HBlink connections table
def build_hblink_table(_config, _stats_table):
    for _hbp, _hbp_data in _config.iteritems():
        if _hbp_data['ENABLED'] == True:

            # Process Master Systems
            if _hbp_data['MODE'] == 'MASTER':
                _stats_table['MASTERS'][_hbp] = {}
                if _hbp_data['REPEAT']:
                    _stats_table['MASTERS'][_hbp]['REPEAT'] = "repeat"
                else:
                    _stats_table['MASTERS'][_hbp]['REPEAT'] = "isolate"
                _stats_table['MASTERS'][_hbp]['PEERS'] = {}
                for _peer in _hbp_data['PEERS']:
                    add_hb_peer(_hbp_data['PEERS'][_peer], _stats_table['MASTERS'][_hbp]['PEERS'], _peer)

            # Proccess Peer Systems
            elif _hbp_data['MODE'] == 'PEER':
                _stats_table['PEERS'][_hbp] = {}
                _stats_table['PEERS'][_hbp]['CALLSIGN'] = _hbp_data['CALLSIGN']
                _stats_table['PEERS'][_hbp]['LOCATION'] = _hbp_data['LOCATION']
                _stats_table['PEERS'][_hbp]['RADIO_ID'] = int_id(_hbp_data['RADIO_ID'])
                _stats_table['PEERS'][_hbp]['MASTER_IP'] = _hbp_data['MASTER_IP']
                _stats_table['PEERS'][_hbp]['MASTER_PORT'] = _hbp_data['MASTER_PORT']
                _stats_table['PEERS'][_hbp]['STATS'] = {}
                _stats_table['PEERS'][_hbp]['STATS']['CONNECTION'] = _hbp_data['STATS']['CONNECTION']
                _stats_table['PEERS'][_hbp]['STATS']['CONNECTED'] = since(_hbp_data['STATS']['CONNECTED'])
                _stats_table['PEERS'][_hbp]['STATS']['PINGS_SENT'] = _hbp_data['STATS']['PINGS_SENT']
                _stats_table['PEERS'][_hbp]['STATS']['PINGS_ACKD'] = _hbp_data['STATS']['PINGS_ACKD']
                if _hbp_data['SLOTS'] == 0:
                    _stats_table['PEERS'][_hbp]['SLOTS'] = 'NONE'
                elif _hbp_data['SLOTS']  <= '2':
                    _stats_table['PEERS'][_hbp]['SLOTS'] = _hbp_data['SLOTS']
                elif _hbp_data['SLOTS']  == '3':
                    _stats_table['PEERS'][_hbp]['SLOTS'] = 'BOTH'
                else:
                    _stats_table['SLOTS'][_hbp]['SLOTS'] = 'DMO'
                   # SLOT 1&2 - for real-time montior: make the structure for later use

                for ts in range(1,3):
                    _stats_table['PEERS'][_hbp][ts]= {}
                    _stats_table['PEERS'][_hbp][ts]['COLOR'] = ''
                    _stats_table['PEERS'][_hbp][ts]['BGCOLOR'] = ''
                    _stats_table['PEERS'][_hbp][ts]['TS'] = ''
                    _stats_table['PEERS'][_hbp][ts]['TYPE'] = ''
                    _stats_table['PEERS'][_hbp][ts]['SUB'] = ''
                    _stats_table['PEERS'][_hbp][ts]['SRC'] = ''
                    _stats_table['PEERS'][_hbp][ts]['DEST'] = ''


            # Process OpenBridge systems
            elif _hbp_data['MODE'] == 'OPENBRIDGE':
                _stats_table['OPENBRIDGES'][_hbp] = {}
                _stats_table['OPENBRIDGES'][_hbp]['NETWORK_ID'] = int_id(_hbp_data['NETWORK_ID'])
                _stats_table['OPENBRIDGES'][_hbp]['TARGET_IP'] = _hbp_data['TARGET_IP']
                _stats_table['OPENBRIDGES'][_hbp]['TARGET_PORT'] = _hbp_data['TARGET_PORT']
                _stats_table['OPENBRIDGES'][_hbp]['STREAMS'] = {}

    #return(_stats_table)

def update_hblink_table(_config, _stats_table):
    # Is there a system in HBlink's config monitor doesn't know about?
    for _hbp in _config:
        if _config[_hbp]['MODE'] == 'MASTER':
            for _peer in _config[_hbp]['PEERS']:
                if int_id(_peer) not in _stats_table['MASTERS'][_hbp]['PEERS'] and _config[_hbp]['PEERS'][_peer]['CONNECTION'] == 'YES':
                    logger.info('Adding peer to CTABLE that has registerred: %s', int_id(_peer))
                    add_hb_peer(_config[_hbp]['PEERS'][_peer], _stats_table['MASTERS'][_hbp]['PEERS'], _peer)

    # Is there a system in monitor that's been removed from HBlink's config?
    for _hbp in _stats_table['MASTERS']:
        remove_list = []
        if _config[_hbp]['MODE'] == 'MASTER':
            for _peer in _stats_table['MASTERS'][_hbp]['PEERS']:
                if hex_str_4(_peer) not in _config[_hbp]['PEERS']:
                    remove_list.append(_peer)

            for _peer in remove_list:
                logger.info('Deleting stats peer not in hblink config: %s', _peer)
                del (_stats_table['MASTERS'][_hbp]['PEERS'][_peer])

    # Update connection time
    for _hbp in _stats_table['MASTERS']:
        for _peer in _stats_table['MASTERS'][_hbp]['PEERS']:
            if hex_str_4(_peer) in _config[_hbp]['PEERS']:
                _stats_table['MASTERS'][_hbp]['PEERS'][_peer]['CONNECTED'] = since(_config[_hbp]['PEERS'][hex_str_4(_peer)]['CONNECTED'])

    for _hbp in _stats_table['PEERS']:
        _stats_table['PEERS'][_hbp]['STATS']['CONNECTED'] = since(_config[_hbp]['STATS']['CONNECTED'])
        _stats_table['PEERS'][_hbp]['STATS']['PINGS_SENT'] = _config[_hbp]['STATS']['PINGS_SENT']
        _stats_table['PEERS'][_hbp]['STATS']['PINGS_ACKD'] = _config[_hbp]['STATS']['PINGS_ACKD']

    build_stats()

#
# CONFBRIDGE TABLE FUNCTIONS
#
def build_bridge_table(_bridges):
    _stats_table = {}
    _now = time()
    _cnow = strftime('%Y-%m-%d %H:%M:%S', localtime(_now))

    for _bridge, _bridge_data in _bridges.iteritems():
        _stats_table[_bridge] = {}

        for system in _bridges[_bridge]:
            _stats_table[_bridge][system['SYSTEM']] = {}
            _stats_table[_bridge][system['SYSTEM']]['TS'] = system['TS']
            _stats_table[_bridge][system['SYSTEM']]['TGID'] = int_id(system['TGID'])

            if system['TO_TYPE'] == 'ON' or system['TO_TYPE'] == 'OFF':
                if system['TIMER'] - _now > 0:
                    _stats_table[_bridge][system['SYSTEM']]['EXP_TIME'] = int(system['TIMER'] - _now)
                else:
                    _stats_table[_bridge][system['SYSTEM']]['EXP_TIME'] = 'Expired'
                if system['TO_TYPE'] == 'ON':
                    _stats_table[_bridge][system['SYSTEM']]['TO_ACTION'] = 'Disconnect'
                else:
                    _stats_table[_bridge][system['SYSTEM']]['TO_ACTION'] = 'Connect'
            else:
                _stats_table[_bridge][system['SYSTEM']]['EXP_TIME'] = 'N/A'
                _stats_table[_bridge][system['SYSTEM']]['TO_ACTION'] = 'None'

            if system['ACTIVE'] == True:
                _stats_table[_bridge][system['SYSTEM']]['ACTIVE'] = 'Connected'
                _stats_table[_bridge][system['SYSTEM']]['COLOR'] = BLACK
                _stats_table[_bridge][system['SYSTEM']]['BGCOLOR'] = GREEN
            elif system['ACTIVE'] == False:
                _stats_table[_bridge][system['SYSTEM']]['ACTIVE'] = 'Disconnected'
                _stats_table[_bridge][system['SYSTEM']]['COLOR'] = WHITE
                _stats_table[_bridge][system['SYSTEM']]['BGCOLOR'] = RED

            for i in range(len(system['ON'])):
                system['ON'][i] = str(int_id(system['ON'][i]))

            _stats_table[_bridge][system['SYSTEM']]['TRIG_ON'] = ', '.join(system['ON'])

            for i in range(len(system['OFF'])):
                system['OFF'][i] = str(int_id(system['OFF'][i]))

            _stats_table[_bridge][system['SYSTEM']]['TRIG_OFF'] = ', '.join(system['OFF'])
    return _stats_table

#
# BUILD HBlink AND CONFBRIDGE TABLES FROM CONFIG/BRIDGES DICTS
#          THIS CURRENTLY IS A TIMED CALL
#
build_time = time()
def build_stats():
    global build_time
    now = time()
    if True: #now > build_time + 1:
        if CONFIG:
            table = 'd' + dtemplate.render(_table=CTABLE)
            dashboard_server.broadcast(table)
        if BRIDGES:
            table = 'b' + btemplate.render(_table=BTABLE['BRIDGES'])
            dashboard_server.broadcast(table)
        build_time = now


def timeout_clients():
    now = time()
    try:
        for client in dashboard_server.clients:
            if dashboard_server.clients[client] + CLIENT_TIMEOUT < now:
                logger.info('TIMEOUT: disconnecting client %s', dashboard_server.clients[client])
                try:
                    dashboard.sendClose(client)
                except Exception as e:
                    logger.error('Exception caught parsing client timeout %s', e)
    except:
        logger.info('CLIENT TIMEOUT: List does not exist, skipping. If this message persists, contact the developer')


def rts_update(p):
    callType = p[0]
    action = p[1]
    trx = p[2]
    system = p[3]
    streamId = p[4]
    sourcePeer = int(p[5])
    sourceSub = int(p[6])
    timeSlot = int(p[7])
    destination = int(p[8])

    if system in CTABLE['MASTERS']:
        for peer in CTABLE['MASTERS'][system]['PEERS']:
            if sourcePeer == peer:
                bgcolor = GREEN
                color = BLACK
            else:
                bgcolor = RED
                color = WHITE

            if action == 'START':
                CTABLE['MASTERS'][system]['PEERS'][peer][timeSlot]['TS'] = True
                CTABLE['MASTERS'][system]['PEERS'][peer][timeSlot]['COLOR'] = color
                CTABLE['MASTERS'][system]['PEERS'][peer][timeSlot]['BGCOLOR'] = bgcolor
                CTABLE['MASTERS'][system]['PEERS'][peer][timeSlot]['TYPE'] = callType
                CTABLE['MASTERS'][system]['PEERS'][peer][timeSlot]['SUB'] = '{} ({})'.format(alias_short(sourceSub, subscriber_ids), sourceSub)
                CTABLE['MASTERS'][system]['PEERS'][peer][timeSlot]['SRC'] = peer
                CTABLE['MASTERS'][system]['PEERS'][peer][timeSlot]['DEST'] = '{}'.format(alias_tgid(destination,talkgroup_ids))
            if action == 'END':
                CTABLE['MASTERS'][system]['PEERS'][peer][timeSlot]['TS'] = False
                CTABLE['MASTERS'][system]['PEERS'][peer][timeSlot]['COLOR'] = BLACK
                CTABLE['MASTERS'][system]['PEERS'][peer][timeSlot]['BGCOLOR'] = WHITE
                CTABLE['MASTERS'][system]['PEERS'][peer][timeSlot]['TYPE'] = ''
                CTABLE['MASTERS'][system]['PEERS'][peer][timeSlot]['SUB'] = ''
                CTABLE['MASTERS'][system]['PEERS'][peer][timeSlot]['SRC'] = ''
                CTABLE['MASTERS'][system]['PEERS'][peer][timeSlot]['DEST'] = ''

    if system in CTABLE['OPENBRIDGES']:
        if action == 'START':
            CTABLE['OPENBRIDGES'][system]['STREAMS'][streamId] = (trx, alias_call(sourceSub, subscriber_ids), destination)
        if action == 'END':
            if streamId in CTABLE['OPENBRIDGES'][system]['STREAMS']:
                del CTABLE['OPENBRIDGES'][system]['STREAMS'][streamId]

    if system in CTABLE['PEERS']:
        bgcolor = GREEN
        if trx == 'RX':
            bgcolor = GREEN
            color = BLACK
        else:
            bgcolor = RED
            color = WHITE

        if action == 'START':
            CTABLE['PEERS'][system][timeSlot]['TS'] = True
            CTABLE['PEERS'][system][timeSlot]['COLOR'] = color
            CTABLE['PEERS'][system][timeSlot]['BGCOLOR'] = bgcolor
            CTABLE['PEERS'][system][timeSlot]['TYPE'] = callType
            CTABLE['PEERS'][system][timeSlot]['SUB'] = '{} ({})'.format(alias_short(sourceSub, subscriber_ids), sourceSub)
            CTABLE['PEERS'][system][timeSlot]['SRC'] = sourcePeer
            CTABLE['PEERS'][system][timeSlot]['DEST'] = '{}'.format(alias_tgid(destination,talkgroup_ids))
        if action == 'END':
            CTABLE['PEERS'][system][timeSlot]['TS'] = False
            CTABLE['PEERS'][system][timeSlot]['COLOR'] = BLACK
            CTABLE['PEERS'][system][timeSlot]['BGCOLOR'] = WHITE
            CTABLE['PEERS'][system][timeSlot]['TYPE'] = ''
            CTABLE['PEERS'][system][timeSlot]['SUB'] = ''
            CTABLE['PEERS'][system][timeSlot]['SRC'] = ''
            CTABLE['PEERS'][system][timeSlot]['DEST'] = ''

    build_stats()

#
# PROCESS IN COMING MESSAGES AND TAKE THE CORRECT ACTION DEPENING ON THE OPCODE
#
def process_message(_message):
    global CTABLE, CONFIG, BRIDGES, CONFIG_RX, BRIDGES_RX
    opcode = _message[:1]
    _now = strftime('%Y-%m-%d %H:%M:%S %Z', localtime(time()))

    if opcode == OPCODE['CONFIG_SND']:
        logging.debug('got CONFIG_SND opcode')
        CONFIG = load_dictionary(_message)
        CONFIG_RX = strftime('%Y-%m-%d %H:%M:%S', localtime(time()))
        if CTABLE['MASTERS']:
            update_hblink_table(CONFIG, CTABLE)
        else:
            build_hblink_table(CONFIG, CTABLE)

    elif opcode == OPCODE['BRIDGE_SND']:
        logging.debug('got BRIDGE_SND opcode')
        BRIDGES = load_dictionary(_message)
        BRIDGES_RX = strftime('%Y-%m-%d %H:%M:%S', localtime(time()))
        BTABLE['BRIDGES'] = build_bridge_table(BRIDGES)

    elif opcode == OPCODE['LINK_EVENT']:
        logging.info('LINK_EVENT Received: {}'.format(repr(_message[1:])))

    elif opcode == OPCODE['BRDG_EVENT']:
        logging.info('BRIDGE EVENT: {}'.format(repr(_message[1:])))
        p = _message[1:].split(",")
        rts_update(p)
        if p[0] == 'GROUP VOICE' and p[2] != 'TX':
            if p[1] == 'END':
                log_message = '{}: {} {}:   SYS: {:12.12s} SRC: {:8.8s}; {:15.15s} TS: {} TGID: {:>5s} {:14.14s} SUB: {:8.8s}; {:30.30s} Time: {}s'.format(_now, p[0], p[1], p[3], p[5], alias_call(int(p[5]), peer_ids), p[7], p[8], alias_tgid(int(p[8]),talkgroup_ids), p[6], alias_short(int(p[6]), subscriber_ids), p[9])
            elif p[1] == 'START':
                log_message = '{}: {} {}: SYS: {:12.12s} SRC: {:8.8s}; {:15.15s} TS: {} TGID: {:>5s} {:14.14s} SUB: {:8.8s}; {:30.30s}'.format(_now, p[0], p[1], p[3], p[5], alias_call(int(p[5]), peer_ids), p[7], p[8], alias_tgid(int(p[8]),talkgroup_ids), p[6], alias_short(int(p[6]), subscriber_ids))
            elif p[1] == 'END WITHOUT MATCHING START':
                log_message = '{}: {} {} on SYSTEM {:12.12s}: SRC: {:8.8s}; {}:15.15s TS: {} TGID: {:>5s} {:14.14s} SUB: {:8.8s}; {:30.30s}'.format(_now, p[0], p[1], p[3], p[5], alias_call(int(p[5]), peer_ids), p[7], p[8], alias_tgid(int(p[8]),talkgroup_ids), p[6], alias_short(int(p[6]), subscriber_ids))
            else:
                log_message = '{}: UNKNOWN GROUP VOICE LOG MESSAGE'.format(_now)

            dashboard_server.broadcast('l' + log_message)
            LOGBUF.append(log_message)

        else:
            logging.debug('{}: UNKNOWN LOG MESSAGE'.format(_now))

    else:
        logging.debug('got unknown opcode: {}, message: {}'.format(repr(opcode), repr(_message[1:])))

def load_dictionary(_message):
    data = _message[1:]
    return loads(data)
    logging.debug('Successfully decoded dictionary')

#
# COMMUNICATION WITH THE HBlink INSTANCE
#
class report(NetstringReceiver):
    def __init__(self):
        pass

    def connectionMade(self):
        pass

    def connectionLost(self, reason):
        pass

    def stringReceived(self, data):
        process_message(data)


class reportClientFactory(ReconnectingClientFactory):
    def __init__(self):
        logging.info('reportClient object for connecting to HBlink.py created at: %s', self)

    def startedConnecting(self, connector):
        logging.info('Initiating Connection to Server.')
        if 'dashboard_server' in locals() or 'dashboard_server' in globals():
            dashboard_server.broadcast('q' + 'Connection to HBlink Established')

    def buildProtocol(self, addr):
        logging.info('Connected.')
        logging.info('Resetting reconnection delay')
        self.resetDelay()
        return report()

    def clientConnectionLost(self, connector, reason):
        logging.info('Lost connection.  Reason: %s', reason)
        ReconnectingClientFactory.clientConnectionLost(self, connector, reason)
        dashboard_server.broadcast('q' + 'Connection to HBlink Lost')

    def clientConnectionFailed(self, connector, reason):
        logging.info('Connection failed. Reason: %s', reason)
        ReconnectingClientFactory.clientConnectionFailed(self, connector, reason)


#
# WEBSOCKET COMMUNICATION WITH THE DASHBOARD CLIENT
#
class dashboard(WebSocketServerProtocol):

    def onConnect(self, request):
        logging.info('Client connecting: %s', request.peer)

    def onOpen(self):
        logging.info('WebSocket connection open.')
        self.factory.register(self)
        self.sendMessage('d' + str(dtemplate.render(_table=CTABLE)))
        self.sendMessage('b' + str(btemplate.render(_table=BTABLE['BRIDGES'])))
        for _message in LOGBUF:
            if _message:
                self.sendMessage('l' + _message)

    def onMessage(self, payload, isBinary):
        if isBinary:
            logging.info('Binary message received: %s bytes', len(payload))
        else:
            logging.info('Text message received: %s', payload.decode('utf8'))

    def connectionLost(self, reason):
        WebSocketServerProtocol.connectionLost(self, reason)
        self.factory.unregister(self)

    def onClose(self, wasClean, code, reason):
        logging.info('WebSocket connection closed: %s', reason)

class dashboardFactory(WebSocketServerFactory):

    def __init__(self, url):
        WebSocketServerFactory.__init__(self, url)
        self.clients = {}

    def register(self, client):
        if client not in self.clients:
            logging.info('registered client %s', client.peer)
            self.clients[client] = time()

    def unregister(self, client):
        if client in self.clients:
            logging.info('unregistered client %s', client.peer)
            del self.clients[client]

    def broadcast(self, msg):
        logging.debug('broadcasting message to: %s', self.clients)
        for c in self.clients:
            c.sendMessage(msg.encode('utf8'))
            logging.debug('message sent to %s', c.peer)

#
# STATIC WEBSERVER
#
class web_server(Resource):
    isLeaf = True
    def render_GET(self, request):
        logging.info('static website requested: %s', request)
        if request.uri == '/':
            return index_html
        else:
            return 'Bad request'




if __name__ == '__main__':
    logging.basicConfig(
        level=logging.INFO,
        filename = (LOG_PATH + LOG_NAME),
        filemode='a',
        format='%(asctime)s %(levelname)s %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    formatter = logging.Formatter('%(asctime)s %(levelname)s %(message)s')
    console.setFormatter(formatter)
    logging.getLogger('').addHandler(console)
    logger = logging.getLogger(__name__)

    logging.info('web_tables.py starting up')
    logger.info('\n\nCopyright (c) 2016, 2017, 2018, 2019\n\tThe Regents of the K0USY Group. All rights reserved.\n')

    # Download alias files
    result = try_download(PATH, PEER_FILE, PEER_URL, (FILE_RELOAD * 86400))
    logging.info(result)

    result = try_download(PATH, SUBSCRIBER_FILE, SUBSCRIBER_URL, (FILE_RELOAD * 86400))
    logging.info(result)

    # Make Alias Dictionaries
    peer_ids = mk_full_id_dict(PATH, PEER_FILE, 'peer')
    if peer_ids:
        logging.info('ID ALIAS MAPPER: peer_ids dictionary is available')

    subscriber_ids = mk_full_id_dict(PATH, SUBSCRIBER_FILE, 'subscriber')
    if subscriber_ids:
        logging.info('ID ALIAS MAPPER: subscriber_ids dictionary is available')

    talkgroup_ids = mk_full_id_dict(PATH, TGID_FILE, 'tgid')
    if talkgroup_ids:
        logging.info('ID ALIAS MAPPER: talkgroup_ids dictionary is available')

    local_subscriber_ids = mk_full_id_dict(PATH, LOCAL_SUB_FILE, 'subscriber')
    if local_subscriber_ids:
        logging.info('ID ALIAS MAPPER: local_subscriber_ids added to subscriber_ids dictionary')
        subscriber_ids.update(local_subscriber_ids)

    local_peer_ids = mk_full_id_dict(PATH, LOCAL_PEER_FILE, 'peer')
    if local_peer_ids:
        logging.info('ID ALIAS MAPPER: local_peer_ids added peer_ids dictionary')
        peer_ids.update(local_peer_ids)

    # Jinja2 Stuff
    env = Environment(
        loader=PackageLoader('web_tables', 'templates'),
        autoescape=select_autoescape(['html', 'xml'])
    )

    dtemplate = env.get_template('hblink_table.html')
    btemplate = env.get_template('bridge_table.html')

    # Create Static Website index file
    index_html = get_template(PATH + 'index_template.html')
    index_html = index_html.replace('<<<system_name>>>', REPORT_NAME)
    if CLIENT_TIMEOUT > 0:
        index_html = index_html.replace('<<<timeout_warning>>>', 'Continuous connections not allowed. Connections time out in {} seconds'.format(CLIENT_TIMEOUT))
    else:
        index_html = index_html.replace('<<<timeout_warning>>>', '')

    # Start update loop
    update_stats = task.LoopingCall(build_stats)
    update_stats.start(FREQUENCY)

    # Start a timout loop
    if CLIENT_TIMEOUT > 0:
        timeout = task.LoopingCall(timeout_clients)
        timeout.start(10)

    # Connect to HBlink
    reactor.connectTCP(HBLINK_IP, HBLINK_PORT, reportClientFactory())

    # Create websocket server to push content to clients
    dashboard_server = dashboardFactory('ws://*:9000')
    dashboard_server.protocol = dashboard
    reactor.listenTCP(9000, dashboard_server)

    # Create static web server to push initial index.html
    website = Site(web_server())
    reactor.listenTCP(WEB_SERVER_PORT, website)

    reactor.run()
