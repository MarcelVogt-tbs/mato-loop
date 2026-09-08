/* Bash 5 / Readline 8 loadable builtin. No command is evaluated by this module. */
#include <config.h>
#include <builtins.h>
#include <shell.h>
#include <quit.h>
#include <readline/readline.h>
#include <readline/keymaps.h>
#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <poll.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <unistd.h>
#include <time.h>

#define LIMIT (512 * 1024)
static int channel = -1, primary, pending, checking, running, submitted, cancelled;
static pid_t owner;
static unsigned long revision, context_revision;
static char *command_id, *draft, *directory;
static char *authorized_request, *authorized_text;
static unsigned long authorized_revision, authorized_context;
static double authorized_deadline;
static int copied_maps;
static unsigned char input[LIMIT], output[LIMIT];
static size_t input_size, output_size;
static Keymap original_map, edit_map, frozen_map;
static rl_hook_func_t *previous_event;
static rl_getc_func_t *previous_getc;
static rl_voidfunc_t *previous_deprep;
extern char *current_prompt_string, *ps1_prompt;

static int event_hook(void);
static int accept_proposal(int count, int key);
static int accept_authorized(int count, int key);
static int ignore_key(int count, int key) { (void)count; (void)key; return 0; }

static void restore_map(void) {
    if (original_map) rl_set_keymap(original_map);
    original_map = NULL;
    /* Readline can still hold a submap on its dispatch stack. Reclaim the
       editing map only when the next stage is at a stable primary prompt. */
}

static void invalidate(int clear) {
    if (pending) {
        cancelled = 1;
        pending = checking = 0;
        revision++;
        if (clear) { rl_replace_line("", 0); rl_point = 0; }
        free(draft); draft = strdup("");
        restore_map();
    }
}

static void flush_output(void) {
    while (channel >= 0 && output_size) {
        ssize_t n = send(channel, output, output_size, MSG_DONTWAIT | MSG_NOSIGNAL);
        if (n > 0) {
            output_size -= (size_t)n;
            memmove(output, output + n, output_size);
        } else if (n < 0 && (errno == EAGAIN || errno == EINTR)) return;
        else { close(channel); channel = -1; invalidate(1); }
    }
}

static void observe(void) {
    char *cwd = getcwd(NULL, 0);
    if (!cwd) cwd = strdup("");
    if (!directory || strcmp(directory, cwd)) {
        free(directory); directory = cwd; context_revision++;
    } else free(cwd);
    if (pending && !checking && rl_line_buffer && (!draft || strcmp(draft, rl_line_buffer))) {
        free(draft); draft = strdup(rl_line_buffer); revision++;
    }
}

static void report(const char *request, const char *error, const char *exit_code) {
    if (channel < 0) return;
    observe();
    int at_primary = primary && (!(rl_readline_state & RL_STATE_READCMD) ||
                                  current_prompt_string == ps1_prompt);
    const char *state = running ? "running" : pending ? (checking ? "checking" : "editing")
                        : at_primary ? "idle" : "busy";
    char rev[32], ctx[32];
    snprintf(rev, sizeof rev, "%lu", revision);
    snprintf(ctx, sizeof ctx, "%lu", context_revision);
    const char *fields[] = {request, error, state, directory, ctx,
        command_id ? command_id : "", draft ? draft : "", rev,
        submitted ? "1" : "0", cancelled ? "1" : "0", exit_code};
    size_t size = 0;
    for (size_t i = 0; i < 11; i++) size += strlen(fields[i]) + 1;
    if (size + 4 > LIMIT - output_size) {
        close(channel); channel = -1; invalidate(1); return;
    }
    uint32_t length = htonl((uint32_t)size);
    memcpy(output + output_size, &length, 4); output_size += 4;
    for (size_t i = 0; i < 11; i++) {
        size_t n = strlen(fields[i]) + 1;
        memcpy(output + output_size, fields[i], n); output_size += n;
    }
    flush_output();
}

/* Only pure Readline editing functions are available during a proposal.
   In particular Bash completion, expansion, bind-x, and keyboard macros cannot
   evaluate unapproved source. Personal bindings are restored unchanged. */
static int safe_function(rl_command_func_t *function) {
    const char *names[] = {
        "self-insert", "quoted-insert", "bracketed-paste-begin", "tab-insert",
        "backward-char", "forward-char", "backward-byte", "forward-byte",
        "beginning-of-line", "end-of-line", "backward-word", "forward-word",
        "backward-delete-char", "delete-char", "rubout",
        "kill-line", "backward-kill-line", "unix-line-discard", "kill-whole-line",
        "kill-word", "backward-kill-word", "unix-word-rubout", "unix-filename-rubout",
        "yank", "yank-pop", "undo", "revert-line", "transpose-chars", "transpose-words",
        "upcase-word", "downcase-word", "capitalize-word", "set-mark", "exchange-point-and-mark",
        "kill-region", "copy-region-as-kill", "copy-backward-word", "copy-forward-word",
        "previous-history", "next-history", "beginning-of-history", "end-of-history",
        "reverse-search-history", "forward-search-history", "history-search-backward",
        "history-search-forward", "non-incremental-reverse-search-history",
        "non-incremental-forward-search-history", "digit-argument", "universal-argument",
        "redraw-current-line", "clear-screen", "abort", NULL
    };
    if (!function) return 0;
    for (int i = 0; names[i]; i++) if (function == rl_named_function(names[i])) return 1;
    return 0;
}

static Keymap filtered_map(Keymap source, int depth) {
    Keymap map = rl_make_bare_keymap();
    copied_maps++;
    for (int i = 0; i < KEYMAP_SIZE; i++) {
        map[i].type = ISFUNC; map[i].function = ignore_key;
        if (source[i].type == ISKMAP && depth < 8 && copied_maps < 64) {
            map[i].type = ISKMAP;
            map[i].function = (rl_command_func_t *)filtered_map((Keymap)source[i].function, depth + 1);
        } else if (source[i].type == ISFUNC) {
            if (source[i].function == rl_newline) map[i].function = accept_proposal;
            else if (safe_function(source[i].function)) map[i].function = source[i].function;
        }
    }
    return map;
}

static int stable_input(void) {
    return current_prompt_string == ps1_prompt &&
        !(rl_readline_state & (RL_STATE_ISEARCH | RL_STATE_NSEARCH | RL_STATE_NUMERICARG |
        RL_STATE_MULTIKEY | RL_STATE_METANEXT | RL_STATE_MOREINPUT | RL_STATE_MACROINPUT |
        RL_STATE_VIMOTION)) && !rl_done;
}

static void freeze(void) {
    observe(); checking = 1;
    rl_set_keymap(frozen_map);
}

static int accept_proposal(int count, int key) {
    (void)count; (void)key;
    if (pending && !authorized_request && !submitted) {
        freeze(); submitted = 1; report("", "", "");
    }
    return 0;
}

static int accept_authorized(int count, int key) {
    (void)count; (void)key;
    if (!authorized_request) return 0;
    observe();
    struct timespec now;
    clock_gettime(CLOCK_MONOTONIC, &now);
    char byte;
    int disconnected = channel < 0 || recv(channel, &byte, 1, MSG_PEEK | MSG_DONTWAIT) == 0;
    const char *error = "";
    if (disconnected || now.tv_sec + now.tv_nsec / 1e9 > authorized_deadline ||
        !pending || !checking || interrupt_state || rl_pending_signal() == SIGINT ||
        revision != authorized_revision || context_revision != authorized_context ||
        strcmp(authorized_text, rl_line_buffer)) error = "terminal.stale";
    else {
        pending = checking = primary = 0; running = 1;
        restore_map();
        rl_newline(1, '\n');
    }
    report(authorized_request, error, "");
    free(authorized_request); authorized_request = NULL;
    free(authorized_text); authorized_text = NULL;
    frozen_map['\n'].function = accept_proposal;
    return 0;
}

static void request(char *data, size_t size) {
    char *field[7], *end = data + size;
    for (int i = 0; i < 7; i++) {
        if (data >= end) return;
        field[i] = data;
        char *nul = memchr(data, 0, (size_t)(end - data));
        if (!nul) return;
        data = nul + 1;
    }
    if (data != end) return;
    const char *id = field[0], *op = field[1], *cid = field[2], *text = field[3];
    const char *error = "";
    observe();
    struct timespec now;
    clock_gettime(CLOCK_MONOTONIC, &now);
    if (strtod(field[6], NULL) < now.tv_sec + now.tv_nsec / 1e9) {
        report(id, "terminal.busy", ""); return;
    }
    if (!strcmp(op, "status")) { report(id, "", ""); return; }
    if (!strcmp(op, "redisplay")) {
        if (!primary || running || !stable_input()) error = "terminal.busy";
        else rl_clear_screen(1, 0);
        report(id, error, ""); return;
    }
    if (!strcmp(op, "stage")) {
        if (rl_editing_mode != 1) error = "operation.unsupported";
        else if (!primary || pending || running || !stable_input() || rl_end ||
            strtoul(field[5], NULL, 10) != context_revision) error = "terminal.busy";
        else if (!*cid || strlen(cid) > 128 || !*text || strlen(text) > 256 * 1024)
            error = "request.invalid";
        else {
            original_map = rl_get_keymap();
            if (edit_map) rl_discard_keymap(edit_map);
            copied_maps = 0;
            edit_map = filtered_map(original_map, 0);
            rl_set_keymap(edit_map);
            free(command_id); command_id = strdup(cid);
            free(draft); draft = NULL;
            pending = 1; checking = submitted = cancelled = 0;
            revision++;
            rl_replace_line(text, 0); rl_point = rl_end;
            rl_redisplay();
        }
    } else if (!command_id || strcmp(cid, command_id) || !pending) error = "terminal.stale";
    else if (!strcmp(op, "cancel")) {
        invalidate(1); rl_redisplay();
    } else if (!primary || !stable_input()) error = "terminal.busy";
    else if (!strcmp(op, "inspect")) freeze();
    else if (!strcmp(op, "snapshot")) { /* Observe without changing the editing state. */ }
    else if (!strcmp(op, "execute")) {
        if (!checking || strcmp(text, draft) || strcmp(text, rl_line_buffer) ||
            strtoul(field[4], NULL, 10) != revision ||
            strtoul(field[5], NULL, 10) != context_revision || rl_pending_signal() == SIGINT)
            error = "terminal.stale";
        else {
            authorized_request = strdup(id); authorized_text = strdup(text);
            authorized_revision = revision; authorized_context = context_revision;
            authorized_deadline = strtod(field[6], NULL);
            frozen_map['\n'].function = accept_authorized;
            /* Dispatch through Readline's own input queue, not the PTY. Calling
               rl_newline from an event hook leaves a second read-key iteration
               in flight; native dispatch avoids that cancellation race. */
            rl_stuff_char('\n');
            return;
        }
    } else error = "request.invalid";
    report(id, error, "");
}

static int event_hook(void) {
    if (previous_event) previous_event();
    if (getpid() != owner) return 0;
    /* Bash normally services this through its transient signal hook. A
       persistent event hook must also honor Bash's pending interrupts. */
    if (interrupt_state) invalidate(0);
    QUIT;
    if (channel < 0) return 0;
    if (rl_pending_signal() == SIGINT) invalidate(1);
    observe(); flush_output();
    struct pollfd keyboard = {fileno(rl_instream), POLLIN, 0};
    if (poll(&keyboard, 1, 0) > 0 && (keyboard.revents & POLLIN)) return 0;
    while (input_size < LIMIT) {
        ssize_t n = recv(channel, input + input_size, LIMIT - input_size, MSG_DONTWAIT);
        if (n > 0) input_size += (size_t)n;
        else if (n == 0) { close(channel); channel = -1; invalidate(1); break; }
        else if (errno == EINTR) continue;
        else break;
    }
    while (channel >= 0 && input_size >= 4) {
        uint32_t size; memcpy(&size, input, 4); size = ntohl(size);
        if (size > LIMIT - 4) { close(channel); channel = -1; invalidate(1); break; }
        if (input_size < size + 4) break;
        request((char *)input + 4, size);
        input_size -= size + 4; memmove(input, input + size + 4, input_size);
        if (authorized_request || running || rl_done) break;
    }
    return 0;
}


static int getc_hook(FILE *stream) {
    if (getpid() == owner) observe();
    return previous_getc ? previous_getc(stream) : rl_getc(stream);
}

static void deprep_hook(void) {
    if (getpid() == owner) {
        /* This also fences PS2 and read -e without tracing shell commands.
           A pending proposal must never escape through another native accept path. */
        invalidate(1);
        primary = 0;
        context_revision++;
        report("", "", "");
    }
    if (previous_deprep) previous_deprep();
}

static int ml_shared_builtin(WORD_LIST *list) {
    if (!list) return EXECUTION_FAILURE;
    const char *op = list->word->word;
    if (!strcmp(op, "init") && list->next && channel < 0) {
        const char *version = get_string_value("BASH_VERSION");
        if (!version || version[0] != '5' || version[1] != '.' ||
            rl_readline_version < 0x0800 || rl_readline_version >= 0x0900) return EXECUTION_FAILURE;
        channel = atoi(list->next->word->word); owner = getpid();
        if (channel < 3 || fcntl(channel, F_GETFD) < 0) return EXECUTION_FAILURE;
        fcntl(channel, F_SETFD, FD_CLOEXEC);
        previous_event = rl_event_hook; rl_event_hook = event_hook;
        previous_getc = rl_getc_function; rl_getc_function = getc_hook;
        previous_deprep = rl_deprep_term_function; rl_deprep_term_function = deprep_hook;
        frozen_map = rl_make_bare_keymap();
        for (int i = 0; i < KEYMAP_SIZE; i++) frozen_map[i].function = ignore_key;
        frozen_map['\r'].function = frozen_map['\n'].function = accept_proposal;
        return EXECUTION_SUCCESS;
    }
    if (channel < 0 || getpid() != owner) return EXECUTION_FAILURE;
    if (!strcmp(op, "prompt")) {
        invalidate(0); running = 0; primary = 1; context_revision++;
        if (rl_event_hook != event_hook) {
            previous_event = rl_event_hook; rl_event_hook = event_hook;
        }
        if (rl_getc_function != getc_hook) {
            previous_getc = rl_getc_function; rl_getc_function = getc_hook;
        }
        if (rl_deprep_term_function != deprep_hook) {
            previous_deprep = rl_deprep_term_function; rl_deprep_term_function = deprep_hook;
        }
        if (authorized_request) {
            report(authorized_request, "terminal.stale", "");
            free(authorized_request); authorized_request = NULL;
            free(authorized_text); authorized_text = NULL;
            frozen_map['\n'].function = accept_proposal;
        }
        report("", "", list->next ? list->next->word->word : "");
    } else return EXECUTION_FAILURE;
    return EXECUTION_SUCCESS;
}

int ml_shared_builtin_load(char *name) { (void)name; return 1; }
void ml_shared_builtin_unload(char *name) {
    (void)name; invalidate(1);
    if (rl_event_hook == event_hook) rl_event_hook = previous_event;
    if (rl_getc_function == getc_hook) rl_getc_function = previous_getc;
    if (rl_deprep_term_function == deprep_hook) rl_deprep_term_function = previous_deprep;
    if (channel >= 0) close(channel);
    channel = -1;
}
static char *documentation[] = {"Managed MatoLoop Readline control channel.", NULL};
struct builtin ml_shared_struct = {
    "ml_shared", ml_shared_builtin, BUILTIN_ENABLED, documentation,
    "ml_shared init FD | prompt STATUS", 0
};
