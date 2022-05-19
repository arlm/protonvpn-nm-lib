import dbus

from ....constants import VIRTUAL_DEVICE_NAME
from ....enums import (ConnectionStartStatusEnum, KillSwitchActionEnum,
                       KillswitchStatusEnum, VPNConnectionReasonEnum,
                       VPNConnectionStateEnum)
from ...environment import ExecutionEnvironment
from ....logger import logger
from ...dbus.dbus_login1_wrapper import Login1UnitWrapper
from ...dbus.dbus_network_manager_wrapper import NetworkManagerUnitWrapper
env = ExecutionEnvironment()


class MonitorVPNConnectionStart:
    def __init__(self, loop, dbus_response):
        self.dbus_response = dbus_response
        self.max_attempts = 5
        self.delay = 5000
        self.failed_attempts = 0
        self.loop = loop
        self.virtual_device_name = VIRTUAL_DEVICE_NAME
        self.bus = dbus.SystemBus()
        self.nm_wrapper = NetworkManagerUnitWrapper(self.bus)
        self.login1_wrapper = Login1UnitWrapper(self.bus)
        self.vpn_check()

    def vpn_check(self):
        vpn_interface = self.nm_wrapper.get_vpn_interface()

        if not isinstance(vpn_interface, tuple):
            self.dbus_response[ConnectionStartStatusEnum.STATE] =\
                VPNConnectionStateEnum(999)
            self.dbus_response[ConnectionStartStatusEnum.MESSAGE] =\
                "No VPN was found"
            self.dbus_response[ConnectionStartStatusEnum.REASON] =\
                VPNConnectionReasonEnum(999)
            self.loop.quit()

        (
            is_protonvpn, state, conn
        ) = self.nm_wrapper.is_protonvpn_being_prepared()
        if is_protonvpn and state == 1:
            self.vpn_signal_handler(conn)

    def on_vpn_state_changed(self, state, reason):
        state = VPNConnectionStateEnum(state)
        reason = VPNConnectionReasonEnum(reason)
        logger.info("State: {} - Reason: {}".format(state, reason))

        if state == VPNConnectionStateEnum.IS_ACTIVE:
            msg = "Successfully connected to Proton VPN."

            if env.settings.killswitch == KillswitchStatusEnum.HARD: # noqa
                env.killswitch.manage(
                    KillSwitchActionEnum.POST_CONNECTION
                )
            elif env.settings.killswitch == KillswitchStatusEnum.SOFT: # noqa
                env.killswitch.manage(KillSwitchActionEnum.SOFT)

            logger.info("State: {} ; Reason{} ; Message: {}".format(
                state, reason, msg
            ))
            self.dbus_response[ConnectionStartStatusEnum.STATE] = state
            self.dbus_response[ConnectionStartStatusEnum.MESSAGE] = msg
            self.dbus_response[ConnectionStartStatusEnum.REASON] = reason

            try:
                env.api_session.update_servers_if_needed()
            except: # noqa
                # Just skip if servers could not be updated
                pass
            logger.info("Quitting loop on active Proton VPN connection")
            self.loop.quit()
        elif state in [
            VPNConnectionStateEnum.FAILED,
            VPNConnectionStateEnum.DISCONNECTED
        ]:

            msg = "Proton VPN connection failed due to "
            reason = VPNConnectionReasonEnum.UNKNOWN_ERROR
            if state == VPNConnectionStateEnum.FAILED:
                if (
                    reason
                    == VPNConnectionReasonEnum.CONN_ATTEMPT_TO_SERVICE_TIMED_OUT # noqa
                ):
                    msg += "VPN connection time out."
                elif (
                    reason
                    == VPNConnectionReasonEnum.SECRETS_WERE_NOT_PROVIDED
                ):
                    msg += "incorrect openvpn credentials."
                else:
                    msg += "unknown reason."
            else:
                msg = msg + "unknown reasons."

            if state == VPNConnectionStateEnum.DISCONNECTED:
                msg = "Proton VPN connection has been disconnected. "\
                    "Reason: {}".format(reason)

            logger.error("State: {} ; Reason{} ; Message: {}".format(
                state, reason, msg
            ))
            self.dbus_response[ConnectionStartStatusEnum.STATE] = state
            self.dbus_response[ConnectionStartStatusEnum.MESSAGE] = msg
            self.dbus_response[ConnectionStartStatusEnum.REASON] = reason
            logger.info("Quitting loop on failed Proton VPN connection")
            self.loop.quit()

    def vpn_signal_handler(self, conn):
        """Add signal handler to Proton VPN connection.

        Args:
            vpn_conn_path (string): path to Proton VPN connection
        """
        proxy = self.bus.get_object(
            "org.freedesktop.NetworkManager", conn
        )
        iface = dbus.Interface(
            proxy, "org.freedesktop.NetworkManager.VPN.Connection"
        )

        try:
            active_conn_props = self.nm_wrapper.get_active_connection_properties(conn)
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
