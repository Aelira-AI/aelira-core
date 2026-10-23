/* CVE-2026-86144 behavioural control. A document parsed with XML_PARSE_NONET
 * and expanded with xmlXIncludeProcess() must not fetch a network text
 * include. A local listener records whether the library sends an HTTP
 * request. Exit 0: no request. Exit 1: request sent. Other: probe error. */
#include <arpa/inet.h>
#include <libxml/parser.h>
#include <libxml/xinclude.h>
#include <netinet/in.h>
#include <signal.h>
#include <stdio.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/wait.h>
#include <unistd.h>

static int process(const char *xml, char **text) {
    xmlDocPtr doc = xmlReadMemory(xml, (int)strlen(xml), "probe.xml", NULL, XML_PARSE_NONET);
    if (doc == NULL) return -1;
    int status = xmlXIncludeProcess(doc);
    if (text) *text = (char *)xmlNodeGetContent(xmlDocGetRootElement(doc));
    xmlFreeDoc(doc);
    return status;
}

int main(int argc, char **argv) {
    char xml[512], *text = NULL;
    if (argc != 2) return 2;
    /* Positive control: a local text include must be expanded. */
    snprintf(xml, sizeof xml, "<r xmlns:xi=\"http://www.w3.org/2001/XInclude\">"
             "<xi:include href=\"file://%s\" parse=\"text\"/></r>", argv[1]);
    if (process(xml, &text) < 1 || text == NULL || strcmp(text, "local-text") != 0) {
        fprintf(stderr, "text XInclude was not processed\n");
        return 2;
    }
    xmlFree(text);

    int server = socket(AF_INET, SOCK_STREAM, 0), one = 1;
    struct sockaddr_in addr = {0};
    socklen_t len = sizeof addr;
    addr.sin_family = AF_INET;
    addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    if (server < 0 || setsockopt(server, SOL_SOCKET, SO_REUSEADDR, &one, sizeof one) ||
        bind(server, (struct sockaddr *)&addr, sizeof addr) || listen(server, 4) ||
        getsockname(server, (struct sockaddr *)&addr, &len)) return 2;
    pid_t pid = fork();
    if (pid < 0) return 2;
    if (pid == 0) {
        alarm(30);
        for (;;) {
            char line[16] = {0};
            int client = accept(server, NULL, NULL);
            if (client < 0) _exit(5);
            if (read(client, line, sizeof line - 1) > 0) {
                if (strncmp(line, "GET ", 4) == 0) _exit(3);
                if (strncmp(line, "SENTINEL", 8) == 0) _exit(0);
            }
            close(client);
        }
    }
    close(server);
    snprintf(xml, sizeof xml, "<r xmlns:xi=\"http://www.w3.org/2001/XInclude\">"
             "<xi:include href=\"http://127.0.0.1:%d/x\" parse=\"text\"/></r>", ntohs(addr.sin_port));
    process(xml, NULL);
    int sentinel = socket(AF_INET, SOCK_STREAM, 0);
    if (connect(sentinel, (struct sockaddr *)&addr, sizeof addr) == 0) {
        if (write(sentinel, "SENTINEL\n", 9) != 9) return 2;
    }
    close(sentinel);
    int status;
    if (waitpid(pid, &status, 0) != pid || !WIFEXITED(status)) return 2;
    if (WEXITSTATUS(status) == 3) { puts("xinclude-nonet-violated"); return 1; }
    if (WEXITSTATUS(status) != 0) return 2;
    puts("xinclude-nonet-ok");
    return 0;
}
