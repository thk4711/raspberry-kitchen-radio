################################################################################
#
# radio-swupdate-progress
#
################################################################################

RADIO_SWUPDATE_PROGRESS_VERSION = 1.0
RADIO_SWUPDATE_PROGRESS_SITE = $(BR2_EXTERNAL_RADIO_PATH)/package/radio-swupdate-progress
RADIO_SWUPDATE_PROGRESS_SITE_METHOD = local
RADIO_SWUPDATE_PROGRESS_DEPENDENCIES = swupdate

define RADIO_SWUPDATE_PROGRESS_BUILD_CMDS
	$(TARGET_CC) $(TARGET_CFLAGS) $(TARGET_LDFLAGS) \
		-I$(STAGING_DIR)/usr/include \
		-o $(@D)/radio-swupdate-progress $(@D)/radio-swupdate-progress.c \
		-L$(STAGING_DIR)/usr/lib -lswupdate
endef

define RADIO_SWUPDATE_PROGRESS_INSTALL_TARGET_CMDS
	$(INSTALL) -D -m 0755 $(@D)/radio-swupdate-progress \
		$(TARGET_DIR)/usr/bin/radio-swupdate-progress
endef

$(eval $(generic-package))