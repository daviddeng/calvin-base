# -*- coding: utf-8 -*-

# Copyright (c) 2015 Ericsson AB
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import re
import time
import datetime
import json
from calvin.Tools import cscompiler as compiler
from calvin.Tools import deployer
from calvin.utilities.calvinlogger import get_logger
from calvin.utilities.calvin_callback import CalvinCB
from calvin.runtime.south.plugins.async import server_connection 
from urlparse import urlparse

_log = get_logger(__name__)

re_get_log = re.compile(r"GET /log\sHTTP/1")
re_get_node_id = re.compile(r"GET /id\sHTTP/1")
re_get_nodes = re.compile(r"GET /nodes\sHTTP/1")
re_get_node = re.compile(
    r"GET /node/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\sHTTP/1")
re_post_peer_setup = re.compile(r"POST /peer_setup\sHTTP/1")
re_get_applications = re.compile(r"GET /applications\sHTTP/1")
re_get_application = re.compile(
    r"GET /application/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\sHTTP/1")
re_del_application = re.compile(
    r"DELETE /application/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\sHTTP/1")
re_post_new_actor = re.compile(r"POST /actor\sHTTP/1")
re_get_actors = re.compile(
    r"GET /actors\sHTTP/1")
re_get_actor = re.compile(
    r"GET /actor/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\sHTTP/1")
re_del_actor = re.compile(
    r"DELETE /actor/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\sHTTP/1")
re_get_actor_report = re.compile(
    r"GET /actor/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/report\sHTTP/1")
re_post_actor_migrate = re.compile(
    r"POST /actor/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/migrate\sHTTP/1")
re_post_actor_disable = re.compile(
    r"POST /actor/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/disable\sHTTP/1")
re_get_port = re.compile(
    r"GET /actor/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/port/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\sHTTP/1")
re_post_connect = re.compile(r"POST /connect\sHTTP/1")
re_set_port_property = re.compile(r"POST /set_port_property\sHTTP/1")
re_post_deploy = re.compile(r"POST /deploy\sHTTP/1")
re_post_disconnect = re.compile(r"POST /disconnect\sHTTP/1")
re_delete_node = re.compile(r"DELETE /node\sHTTP/1")


_calvincontrol = None


def get_calvincontrol():
    """ Returns the CalvinControl singleton
    """
    global _calvincontrol
    if _calvincontrol is None:
        _calvincontrol = CalvinControl()
    return _calvincontrol


class CalvinControl(object):

    """ A HTTP REST API for calvin nodes
    """

    def __init__(self):
        self.node = None
        self.log_connection = None
        self.routes = None
        self.server = None
        self.connections = {}

    def start(self, node, uri):
        """ Start listening and handle request on uri
        """
        self.port = int(urlparse(uri).port)
        self.host = urlparse(uri).hostname
        _log.info("Listening on: %s:%s" % (self.host, self.port))

        self.node = node

        # Set routes for requests
        self.routes = [
            (re_get_log, self.handle_get_log),
            (re_get_node_id, self.handle_get_node_id),
            (re_get_nodes, self.handle_get_nodes),
            (re_get_node, self.handle_get_node),
            (re_post_peer_setup, self.handle_peer_setup),
            (re_get_applications, self.handle_get_applications),
            (re_get_application, self.handle_get_application),
            (re_del_application, self.handle_del_application),
            (re_post_new_actor, self.handle_new_actor),
            (re_get_actors, self.handle_get_actors),
            (re_get_actor, self.handle_get_actor),
            (re_del_actor, self.handle_del_actor),
            (re_get_actor_report, self.handle_get_actor_report),
            (re_post_actor_migrate, self.handle_actor_migrate),
            (re_post_actor_disable, self.handle_actor_disable),
            (re_get_port, self.handle_get_port),
            (re_post_connect, self.handle_connect),
            (re_set_port_property, self.handle_set_port_property),
            (re_post_deploy, self.handle_deploy),
            (re_delete_node, self.handle_quit),
            (re_post_disconnect, self.handle_disconnect)
        ]
        self.server = server_connection.ServerProtocolFactory(self.node.sched.trigger_loop, "raw")
        self.server.start(self.host, self.port)

    def stop(self):
        """ Stop
        """
        self.server.stop()

    def handle_request(self):
        """ Handle incoming requests
        """
        if self.server.pending_connections:
            addr, conn = self.server.accept()
            self.connections[addr] = conn

        for handle, connection in self.connections.items():
            if connection.data_available:
                data = connection.data_get()
                found = False
                for route in self.routes:
                    match = route[0].match(data)
                    if match:
                        http_data = data.split("\r\n\r\n")[1]
                        if http_data:
                            http_data = json.loads(http_data)
                        _log.debug("Calvin control handles:\n%s\n---------------" % data)
                        route[1](handle, connection, match, http_data)
                        found = True
                        break

                if not found:
                    _log.error("No route found for: %s" % data)
                    self.send_response(
                        handle, connection, "HTTP/1.0 404 Not Found\r\n")

    def send_response(self, handle, connection, data):
        """ Send response header text/html
        """
        if not connection.connection_lost:
            connection.send("HTTP/1.0 200 OK\n"
                            + "Content-Type: application/json\n"
                            +
                            "Access-Control-Allow-Methods: GET, POST, PUT, DELETE, OPTIONS\n"
                            + "Access-Control-Allow-Origin: *\r\n"
                            + "\n")
            connection.send(data)
            connection.close()
        del self.connections[handle]

    def send_streamheader(self, connection):
        """ Send response header for text/event-stream
        """
        if not connection.connection_lost:
            connection.send("HTTP/1.0 200 OK\n"
                               + "Content-Type: text/event-stream\n"
                               + "Access-Control-Allow-Origin: *\r\n"
                               + "\n")

    def storage_cb(self, key, value, handle, connection):
        self.send_response(handle, connection, json.dumps(value))

    def handle_get_log(self, handle, connection, match, data):
        """ Get log stream
        """
        self.log_connection = connection
        self.send_streamheader(connection)

    def handle_get_node_id(self, handle, connection, match, data):
        """ Get node id from this node
        """
        self.send_response(
            handle, connection, json.dumps({'id': self.node.id}))

    def handle_peer_setup(self, handle, connection, match, data):
        self.node.peersetup(data['peers'])
        self.send_response(handle, connection, json.dumps({'result': 'OK'}))

    def handle_get_nodes(self, handle, connection, match, data):
        """ Get active nodes
        """
        self.send_response(
            handle, connection, json.dumps(self.node.network.list_links()))

    def handle_get_node(self, handle, connection, match, data):
        """ Get node information from id
        """
        self.node.storage.get_node(match.group(1), CalvinCB(
            func=self.storage_cb, handle=handle, connection=connection))

    def handle_get_applications(self, handle, connection, match, data):
        """ Get applications
        """
        self.send_response(
            handle, connection, json.dumps(self.node.app_manager.list_applications()))

    def handle_get_application(self, handle, connection, match, data):
        """ Get application from id
        """
        self.node.storage.get_application(match.group(1), CalvinCB(
            func=self.storage_cb, handle=handle, connection=connection))

    def handle_del_application(self, handle, connection, match, data):
        """ Delete application from id
        """
        self.node.app_manager.destroy(match.group(1))
        self.send_response(handle, connection, json.dumps({'result': 'OK'}))

    def handle_new_actor(self, handle, connection, match, data):
        """ Create actor
        """
        actor_id = self.node.new(actor_type=data['actor_type'], args=data[
                                 'args'], deploy_args=data['deploy_args'])
        self.send_response(
            handle, connection, json.dumps({'actor_id': actor_id}))

    def handle_get_actors(self, handle, connection, match, data):
        """ Get actor list
        """
        actors = self.node.am.list_actors()
        self.send_response(
            handle, connection, json.dumps(actors))

    def handle_get_actor(self, handle, connection, match, data):
        """ Get actor from id
        """
        self.node.storage.get_actor(match.group(1), CalvinCB(
            func=self.storage_cb, handle=handle, connection=connection))

    def handle_del_actor(self, handle, connection, match, data):
        """ Delete actor from id
        """
        self.node.am.destroy(match.group(1))
        self.send_response(handle, connection, json.dumps({'result': 'OK'}))

    def handle_get_actor_report(self, handle, connection, match, data):
        """ Get report from actor
        """
        self.send_response(
            handle, connection, json.dumps(self.node.am.report(match.group(1))))

    def handle_actor_migrate(self, handle, connection, match, data):
        """ Migrate actor
        """
        self.node.am.migrate(match.group(1), data['peer_node_id'], 
                             callback=CalvinCB(self.actor_migrate_cb, handle, connection))

    def actor_migrate_cb(self, handle, connection, status, *args, **kwargs):
        """ Migrate actor respons 
        """
        self.send_response(handle, connection, json.dumps({'result': status}))

    def handle_actor_disable(self, handle, connection, match, data):
        self.node.am.disable(match.group(1))
        self.send_response(handle, connection, json.dumps({'result': 'OK'}))

    def handle_get_port(self, handle, connection, match, data):
        """ Get port from id
        """
        self.node.storage.get_port(match.group(2), CalvinCB(
            func=self.storage_cb, handle=handle, connection=connection))

    def handle_connect(self, handle, connection, match, data):
        """ Connect port
        """
        if "actor_id" not in data:
            data["actor_id"] = None
        if "port_name" not in data:
            data["port_name"] = None
        if "port_dir" not in data:
            data["port_dir"] = None
        if "port_id" not in data:
            data["port_id"] = None
        if "peer_node_id" not in data:
            data["peer_node_id"] = None
        if "peer_actor_id" not in data:
            data["peer_actor_id"] = None
        if "peer_port_name" not in data:
            data["peer_port_name"] = None
        if "peer_port_dir" not in data:
            data["peer_port_dir"] = None
        if "peer_port_id" not in data:
            data["peer_port_id"] = None

        self.node.connect(
            actor_id=data["actor_id"],
            port_name=data["port_name"],
            port_dir=data["port_dir"],
            port_id=data["port_id"],
            peer_node_id=data["peer_node_id"],
            peer_actor_id=data["peer_actor_id"],
            peer_port_name=data["peer_port_name"],
            peer_port_dir=data["peer_port_dir"],
            peer_port_id=data["peer_port_id"])

        self.send_response(handle, connection, json.dumps({'result': 'OK'}))

    def handle_set_port_property(self, handle, connection, match, data):
        self.node.am.set_port_property(
            actor_id=data["actor_id"],
            port_type=data["port_type"],
            port_name=data["port_name"],
            port_property=data["port_property"],
            value=data["value"])
        self.send_response(handle, connection, json.dumps({'result': 'OK'}))

    def handle_deploy(self, handle, connection, match, data):
        app_info, errors, warnings = compiler.compile(
            data["script"], filename=data["name"])
        app_info["name"] = data["name"]
        d = deployer.Deployer(
            runtime=None, deployable=app_info, node_info=None, node=self.node)
        app_id = d.deploy()
        self.send_response(
            handle, connection, json.dumps({'application_id': app_id}))

    def handle_quit(self, handle, connection, match, data):
        self.node.stop()
        self.send_response(handle, connection, json.dumps({'result': 'OK'}))

    def handle_disconnect(self, handle, connection, match, data):
        self.node.disconnect(
            data['actor_id'], data['port_name'], data['port_dir'], data['port_id'])
        self.send_response(handle, connection, json.dumps({'result': 'OK'}))

    def log_firing(self, actor_name, action_method, tokens_produced, tokens_consumed, production):
        """ Trace firing, sends data on log_sock
        """
        if self.log_connection is not None:
            ts = time.time()
            st = datetime.datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M:%S')
            data = {}
            data['timestamp'] = st
            data['node_id'] = self.node.id
            data['type'] = 'fire'
            data['actor'] = actor_name
            data['action_method'] = action_method
            data['produced'] = tokens_produced
            data['consumed'] = tokens_consumed
            self.log_connection.send("data: %s\n\n" % json.dumps(data))
