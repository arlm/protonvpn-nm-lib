import datetime
import getpass
import inspect
import sys
import time
from textwrap import dedent

import dbus
from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import GLib
from .. import exceptions
from ..constants import SUPPORTED_FEATURES
from ..enums import ConnectionMetadataEnum, ProtocolEnum
from ..logger import logger
from ..services import capture_exception
from ..services.certificate_manager import CertificateManager
from ..services.connection_manager import ConnectionManager
from ..services.dbus_get_wrapper import DbusGetWrapper
from ..services.server_manager import ServerManager
from ..services.user_manager import UserManager

from .cli_dialog import dialog  # noqa


class CLIWrapper():
    connection_manager = ConnectionManager()
    user_manager = UserManager()
    server_manager = ServerManager(CertificateManager())

    def connect(self, args):
        """Proxymethdo to connect to ProtonVPN."""
        cli_commands = dict(
            servername=self.server_manager.direct,
            fastest=self.server_manager.fastest,
            random=self.server_manager.random_c,
            cc=self.server_manager.country_f,
            sc=self.server_manager.feature_f,
            p2p=self.server_manager.feature_f,
            tor=self.server_manager.feature_f,
        )
        command = False
        exit_type = 1
        protocol = self.determine_protocol(args)

        session = self.get_existing_session(exit_type)

        self.remove_existing_connection()

        for cls_attr in inspect.getmembers(args):
            if cls_attr[0] in cli_commands and cls_attr[1]:
                command = list(cls_attr)

        logger.info("CLI connect type: {}".format(command))

        certificate_filename, domain = self.get_cert_filename_and_domain(
            cli_commands, session, protocol, command
        )
        openvpn_username, openvpn_password = self.get_ovpn_credentials(
            session, exit_type
        )

        logger.info("OpenVPN credentials were fetched.")

        self.add_vpn_connection(
            certificate_filename, openvpn_username, openvpn_password,
            domain, exit_type
        )

        conn_status = self.connection_manager.display_connection_status(
            "all_connections"
        )
        print("Connecting to ProtonVPN on {} with {}...".format(
            conn_status[ConnectionMetadataEnum.SERVER],
            conn_status[ConnectionMetadataEnum.PROTOCOL].upper(),
        ))

        self.connection_manager.start_connection()
        DBusGMainLoop(set_as_default=True)
        loop = GLib.MainLoop()
        MonitorVPNState("proton0", loop)
        loop.run()
        sys.exit(exit_type)

    def disconnect(self):
        """Proxymethod to disconnect from ProtonVPN."""
        print("Disconnecting from ProtonVPN...")

        exit_type = 1
        try:
            self.connection_manager.remove_connection()
        except exceptions.ConnectionNotFound as e:
            print("[!] Unable to disconnect: {}".format(e))
        except (
            exceptions.RemoveConnectionFinishError,
            exceptions.StopConnectionFinishError
        ) as e:
            print("[!] Unable to disconnect: {}".format(e))
        except Exception as e:
            capture_exception(e)
            logger.exception(
                "[!] Unknown error: {}".format(e)
            )
            print("[!] Unknown error occured: {}".format(e))
        else:
            exit_type = 1
            print("\nSuccessfully disconnected from ProtonVPN!")
        finally:
            sys.exit(exit_type)

    def login(self):
        """Proxymethod to login user with ProtonVPN credentials."""
        exit_type = 1
        if self.get_existing_session(exit_type, is_connecting=False):
            print("\nYou are already logged in!")
            sys.exit()

        protonvpn_username = input("\nEnter your ProtonVPN username: ")
        protonvpn_password = getpass.getpass("Enter your ProtonVPN password: ")
        self.login_user(exit_type, protonvpn_username, protonvpn_password)

    def logout(self):
        """Proxymethod to logout user."""
        print("Logging out...")
        exit_type = 1

        self.remove_existing_connection()
        print()

        try:
            self.user_manager.delete_user_session()
        except exceptions.StoredSessionNotFound:
            print("[!] Unable to logout. No session was found.")
        except exceptions.AccessKeyringError:
            print("[!] Unable to logout. Could not access keyring.")
        except Exception as e:
            capture_exception(e)
            logger.exception(
                "[!] Unknown error: {}".format(e)
            )
            print("[!] Unknown error occured: {}.".format(e))
        else:
            exit_type = 0
            logger.info("Successful logout.")
            print("Logout successful!")
        finally:
            sys.exit(exit_type)

    def status(self):
        """Proxymethod to diplay connection status."""
        conn_status = self.connection_manager.display_connection_status()
        if not conn_status:
            print("[!] No active ProtonVPN connection.")
            sys.exit()

        country, load, features = self.extract_server_info(
            conn_status[ConnectionMetadataEnum.SERVER]
        )

        status_to_print = dedent("""
        ProtonVPN Connection Status
        ---------------------------
        Country: {country}
        Server: {server}
        Load: {load}%
        Protocol: {proto}
        Feature(s): {features}
        Connection time: {time}\
        """).format(
            country=country,
            server=conn_status[ConnectionMetadataEnum.SERVER],
            proto=conn_status[ConnectionMetadataEnum.PROTOCOL].upper(),
            time=self.convert_time(
                conn_status[ConnectionMetadataEnum.CONNECTED_TIME]
            ),
            load=load,
            features=", ".join(features)
        )

        print(status_to_print)
        sys.exit()

    def extract_server_info(self, servername):
        """Extract server information to be displayed.

        Args:
            servername (string): servername [PT#1]

        Returns:
            tuple: (country, load, features_list)
        """
        self.server_manager.cache_servers(
            session=self.get_existing_session()
        )

        servers = self.server_manager.extract_server_list()
        country_code = self.server_manager.extract_server_value(
            servername, "ExitCountry", servers
        )
        country = self.server_manager.extract_country_name(country_code)
        load = self.server_manager.extract_server_value(
            servername, "Load", servers
        )
        features = [
            self.server_manager.extract_server_value(
                servername, "Features", servers
            )
        ]

        features_list = []
        for feature in features:
            if feature in SUPPORTED_FEATURES:
                features_list.append(SUPPORTED_FEATURES[feature])

        return country, load, features_list

    def convert_time(self, connected_time):
        """Convert time from epoch to 24h.

        Args:
            connected time (string): time in seconds since epoch

        Returns:
            string: time in 24h format, since last connection was made
        """
        connection_time = (
            time.time()
            - int(connected_time)
        )
        return str(
            datetime.timedelta(
                seconds=connection_time
            )
        ).split(".")[0]

    def add_vpn_connection(
        self, certificate_filename, openvpn_username,
        openvpn_password, domain, exit_type
    ):
        """Proxymethod to add ProtonVPN connection."""
        print("Adding ProtonVPN connection...")
        try:
            self.connection_manager.add_connection(
                certificate_filename, openvpn_username,
                openvpn_password, CertificateManager.delete_cached_certificate,
                domain
            )
        except exceptions.ImportConnectionError as e:
            logger.exception("[!] ImportConnectionError: {}".format(e))
            print("[!] An error occured upon importing connection: ", e)
        except Exception as e:
            capture_exception(e)
            logger.exception(
                "[!] Unknown error: {}".format(e)
            )
            print("[!] Unknown error: {}".format(e))
            sys.exit(exit_type)
        else:
            exit_type = 0

        print(
            "ProtonVPN connection was successfully added to Network Manager."
        )

    def get_ovpn_credentials(self, session, exit_type):
        """Proxymethod to get user OVPN credentials."""
        openvpn_username, openvpn_password = None, None
        try:
            openvpn_username, openvpn_password = self.user_manager.fetch_vpn_credentials( # noqa
                session
            )
        except exceptions.JSONAuthDataEmptyError:
            print(
                "[!] The stored session might be corrupted. "
                + "Please, try to login again."
            )
            sys.exit(exit_type)
        except (
            exceptions.JSONAuthDataError,
            exceptions.JSONAuthDataNoneError
        ):
            print("[!] There is no stored session. Please, login first.")
            sys.exit(exit_type)
        except Exception as e:
            capture_exception(e)
            logger.exception(
                "[!] Unknown error: {}".format(e)
            )
            print("[!] Unknown error occured: {}.".format(e))
            sys.exit(exit_type)

        return openvpn_username, openvpn_password

    def get_cert_filename_and_domain(
        self, cli_commands, session,
        protocol, command
    ):
        """Proxymethod to get certficate filename and server domain."""
        try:
            invoke_dialog = command[0]
        except TypeError:
            servername, protocol = dialog(
                self.server_manager,
                session,
            )

            return self.server_manager.direct(
                session, protocol, servername
            )

        try:
            return cli_commands[command[0]](
                session, protocol, command
            )
        except KeyError as e:
            print("KeyError: {}".format(e))
            sys.exit(1)
        except TypeError as e:
            print("TypeError: {}".format(e))
            sys.exit(1)
        except ValueError as e:
            print("ValueError: {}".format(e))
            sys.exit(1)
        except exceptions.EmptyServerListError as e:
            print(
                "[!] {} This could mean that the ".format(e)
                + "server(s) are under maintenance or "
                + "inaccessible with your plan."
            )
            sys.exit(1)
        except exceptions.IllegalServername as e:
            print("[!] {}".format(e))
            sys.exit(1)

    def determine_protocol(self, args):
        """Determine protocol based on CLI input arguments."""
        protocol = ProtocolEnum.TCP
        try:
            protocol = args.protocol.lower().strip()
        except AttributeError:
            pass
        else:
            delattr(args, "protocol")

        return protocol

    def get_existing_session(self, exit_type=1, is_connecting=True):
        """Proxymethod to get user session."""
        session_exists = False

        try:
            session = self.user_manager.load_session()
        except exceptions.JSONAuthDataEmptyError:
            print(
                "[!] The stored session might be corrupted. "
                + "Please, try to login again."
            )
            if is_connecting:
                sys.exit(exit_type)
        except (
            exceptions.JSONAuthDataError,
            exceptions.JSONAuthDataNoneError
        ):
            if is_connecting:
                print("\n[!] There is no stored session. Please, login first.")
                sys.exit(exit_type)
        except exceptions.AccessKeyringError:
            print(
                "[!] Unable to load session. Could not access keyring."
            )
            if is_connecting:
                sys.exit(exit_type)
        except Exception as e:
            capture_exception(e)
            logger.exception(
                "[!] Unknown error: {}".format(e)
            )
            print("[!] Unknown error occured: {}.".format(e))
            if is_connecting:
                sys.exit(exit_type)
        else:
            session_exists = True
            logger.info("Local session was found.")

        if is_connecting:
            return session

        return session_exists

    def login_user(self, exit_type, protonvpn_username, protonvpn_password):

        print("Attempting to login...")
        try:
            self.user_manager.login(protonvpn_username, protonvpn_password)
        except (TypeError, ValueError) as e:
            print("[!] Unable to authenticate. {}".format(e))
        except exceptions.IncorrectCredentialsError:
            print(
                "[!] Unable to authenticate. "
                + "The provided credentials are incorrect."
            )
        except exceptions.APIAuthenticationError:
            print("[!] Unable to authenticate. Unexpected API response.")
        except Exception as e:
            capture_exception(e)
            logger.exception(
                "[!] Unknown error: {}".format(e)
            )
            print("[!] Unknown error occured: {}".format(e))
        else:
            exit_type = 0
            logger.info("Successful login.")
            print("\nLogin successful!")
        finally:
            sys.exit(exit_type)

    def remove_existing_connection(self):
        try:
            self.connection_manager.remove_connection()
        except exceptions.ConnectionNotFound:
            pass
        else:
            print("Disconnected from ProtonVPN connection.")


class MonitorVPNState(DbusGetWrapper):
    def __init__(self, virtual_device_name, loop):
        self.max_attempts = 5
        self.delay = 5000
        self.failed_attempts = 0
        self.loop = loop
        self.virtual_device_name = virtual_device_name
        self.bus = dbus.SystemBus()
        self.test()

    def test(self):
        vpn_interface = self.get_vpn_interface(True)

        if not isinstance(vpn_interface, tuple):
            print("[!] No VPN was found")
            sys.exit()

        is_protonvpn, state, conn = self.is_protonvpn_being_prepared()
        if is_protonvpn and state == 1:
            self.vpn_signal_handler(conn)

    def on_vpn_state_changed(self, state, reason):
        logger.info("State: {} - Reason: {}".format(state, reason))
        if state == 5:
            logger.info("Successfully connected to ProtonVPN!")
            print("\nSuccessfully connected to ProtonVPN!")
            self.loop.quit()
        elif state in [6, 7]:

            msg = "[!] ProtonVPN connection failed due to "
            if state == 6:
                if reason == 6:
                    msg += "VPN connection time out."
                if reason == 9:
                    msg += "incorrect openvpn credentials."

            if state == 7:
                msg = "[!] ProtonVPN connection has been disconnected. "\
                    "Reason: {}".format(reason)

            logger.error(msg)
            print(msg)
            self.loop.quit()

    def vpn_signal_handler(self, conn):
        """Add signal handler to ProtonVPN connection.

        Args:
            vpn_conn_path (string): path to ProtonVPN connection
        """
        proxy = self.bus.get_object(
            "org.freedesktop.NetworkManager", conn
        )
        iface = dbus.Interface(
            proxy, "org.freedesktop.NetworkManager.VPN.Connection"
        )

        try:
            active_conn_props = self.get_active_conn_props(conn)
            logger.info("Adding listener to active {} connection at {}".format(
                active_conn_props["Id"],
                conn)
            )
        except dbus.exceptions.DBusException:
            logger.info(
                "{} is not an active connection.".format(conn)
            )
        else:
            iface.connect_to_signal(
                "VpnStateChanged", self.on_vpn_state_changed
            )
