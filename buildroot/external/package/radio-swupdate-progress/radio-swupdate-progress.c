/* Machine-readable bridge for SWUpdate 2025.05 progress IPC. */
#include <stdio.h>
#include <string.h>

#include <progress_ipc.h>

static const char *source_name(sourcetype source)
{
	switch (source) {
	case SOURCE_WEBSERVER:
		return "webserver";
	case SOURCE_SURICATTA:
		return "suricatta";
	case SOURCE_DOWNLOADER:
		return "downloader";
	case SOURCE_LOCAL:
		return "local";
	case SOURCE_CHUNKS_DOWNLOADER:
		return "chunks";
	default:
		return "unknown";
	}
}

static void sanitize(char *text)
{
	for (; *text; text++) {
		if (*text == '\t' || *text == '\r' || *text == '\n')
			*text = '_';
	}
}

int main(void)
{
	struct progress_msg message;
	int connection = progress_ipc_connect_with_path(
		"/run/swupdate/swupdateprog", true);

	if (connection < 0)
		return 1;

	for (;;) {
		int received;
		memset(&message, 0, sizeof(message));
		received = progress_ipc_receive(&connection, &message);
		if (received <= 0)
			return 1;
		message.cur_image[sizeof(message.cur_image) - 1] = '\0';
		sanitize(message.cur_image);
		printf("PROGRESS\t%u\t%u\t%u\t%u\t%s\t%s\n",
		       (unsigned int)message.status,
		       message.cur_step,
		       message.nsteps,
		       message.cur_percent,
		       message.cur_image,
		       source_name(message.source));
		fflush(stdout);
		if (message.status == SUCCESS)
			return 0;
		if (message.status == FAILURE)
			return 2;
	}
}