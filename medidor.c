/*
 * medidor.c — executa um comando e grava seus recursos (wait4).
 *
 *   medidor <arquivo_saida> <comando> [args...]
 *
 * Grava em <arquivo_saida>: "<user_us> <sys_us> <maxrss_kb>".
 *
 * Existe porque processos criados diretamente pelo Python herdam, no
 * ru_maxrss, o pico de memória do próprio interpretador (vfork + exec).
 * Aqui o comando é criado por fork() a partir deste processo pequeno,
 * então o pico de RSS reflete só o comando medido.
 *
 * Compilado automaticamente pelo orquestrador.py.
 */
#include <signal.h>
#include <stdio.h>
#include <sys/resource.h>
#include <sys/wait.h>
#include <unistd.h>

static volatile pid_t filho = -1;

static void repassar(int sinal)
{
    if (filho > 0)
        kill(filho, SIGKILL);
    (void)sinal;
}

int main(int argc, char **argv)
{
    if (argc < 3) {
        fprintf(stderr, "uso: medidor <saida> <comando> [args...]\n");
        return 127;
    }

    signal(SIGTERM, repassar);

    filho = fork();
    if (filho < 0)
        return 127;
    if (filho == 0) {
        execvp(argv[2], argv + 2);
        _exit(127);
    }

    int status;
    struct rusage uso;
    while (wait4(filho, &status, 0, &uso) < 0)
        ;

    FILE *f = fopen(argv[1], "w");
    if (f) {
        fprintf(f, "%ld %ld %ld\n",
                (long)(uso.ru_utime.tv_sec * 1000000L + uso.ru_utime.tv_usec),
                (long)(uso.ru_stime.tv_sec * 1000000L + uso.ru_stime.tv_usec),
                (long)uso.ru_maxrss);
        fclose(f);
    }

    if (WIFEXITED(status))
        return WEXITSTATUS(status);
    if (WIFSIGNALED(status))
        return 128 + WTERMSIG(status);
    return 127;
}
