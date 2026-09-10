################################################################################
# radio-equalizer
################################################################################

RADIO_EQUALIZER_VERSION = 1.0
RADIO_EQUALIZER_SITE = $(BR2_EXTERNAL_RADIO_PATH)/package/radio-equalizer
RADIO_EQUALIZER_SITE_METHOD = local
RADIO_EQUALIZER_LICENSE = MIT
RADIO_EQUALIZER_LICENSE_FILES = LICENSE

define RADIO_EQUALIZER_BUILD_CMDS
	$(TARGET_CC) $(TARGET_CFLAGS) -std=c99 -fPIC -Wall -Wextra -shared \
		-o $(@D)/radio_equalizer.so $(@D)/radio_equalizer.c -lm
endef

define RADIO_EQUALIZER_INSTALL_TARGET_CMDS
	$(INSTALL) -D -m 0755 $(@D)/radio_equalizer.so \
		$(TARGET_DIR)/usr/lib/ladspa/radio_equalizer.so
endef

$(eval $(generic-package))