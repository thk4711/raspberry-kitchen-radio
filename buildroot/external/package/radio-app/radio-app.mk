################################################################################
#
# radio-app  (Raspberry Kitchen Radio application)
#
# The app source lives in this repository (the BR2_EXTERNAL tree parent). We
# install radio.py + lib/ + the *.conf files into /opt/raspberry-kitchen-radio
# on the target, and drop the BusyBox init scripts.
#
################################################################################

RADIO_APP_VERSION = 1.0
RADIO_APP_SITE_METHOD = local
# BR2_EXTERNAL_RADIO_PATH points at .../buildroot/external; the app repo root is
# two levels up.
RADIO_APP_SITE = $(BR2_EXTERNAL_RADIO_PATH)/../..
RADIO_APP_LICENSE = MIT
RADIO_APP_LICENSE_FILES = LICENSE

RADIO_APP_TARGET_DIR = /opt/raspberry-kitchen-radio

define RADIO_APP_INSTALL_TARGET_CMDS
	# Application code + libraries + committed config/assets.
	# Remove the old app tree first so incremental Buildroot reinstalls cannot
	# leave stale files or accidentally create /opt/.../lib/lib.
	rm -rf $(TARGET_DIR)$(RADIO_APP_TARGET_DIR)
	$(INSTALL) -d $(TARGET_DIR)$(RADIO_APP_TARGET_DIR)
	$(INSTALL) -m 0755 $(@D)/radio.py $(TARGET_DIR)$(RADIO_APP_TARGET_DIR)/radio.py
	$(INSTALL) -m 0644 $(@D)/radio.conf $(TARGET_DIR)$(RADIO_APP_TARGET_DIR)/radio.conf
	$(INSTALL) -d $(TARGET_DIR)$(RADIO_APP_TARGET_DIR)/lib
	cp -a $(@D)/lib/. $(TARGET_DIR)$(RADIO_APP_TARGET_DIR)/lib/
	# The media backends (shairport-sync, nqptp, go-librespot) are built from
	# source by Buildroot and referenced on PATH; the repo ships no binaries
	# for them. Only the UI font and the station logos under lib/ are assets.
	# Strip any Python bytecode caches copied from the dev tree.
	find $(TARGET_DIR)$(RADIO_APP_TARGET_DIR) -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null || true
endef

define RADIO_APP_INSTALL_INIT_SYSV
	$(INSTALL) -D -m 0755 $(BR2_EXTERNAL_RADIO_PATH)/board/radio/rootfs-overlay/etc/init.d/S90radio \
		$(TARGET_DIR)/etc/init.d/S90radio
	$(INSTALL) -D -m 0755 $(BR2_EXTERNAL_RADIO_PATH)/board/radio/rootfs-overlay/etc/init.d/S50mpd \
		$(TARGET_DIR)/etc/init.d/S50mpd
endef

$(eval $(generic-package))
