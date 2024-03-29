import logging
from itertools import chain
from dataclasses import dataclass, fields
from argparse import ArgumentParser
from datetime import datetime, timezone
from functools import wraps
from collections import defaultdict
from contextvars import ContextVar


import inject
from docker import from_env, DockerClient
from telegram import Update, BotCommandScopeChat, BotCommandScopeDefault, BotCommand
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, filters, Application
from telegram.helpers import escape_markdown
from telegram.constants import ParseMode

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
field_names = lambda datclass: ', '.join(f.name for f in fields(datclass))
bot_cmd_ctx = ContextVar('bot_cmd')
handler_args_ctx = ContextVar('handler_args')
HANDLERS = defaultdict(list)
SCOPES = defaultdict(list)


class FileSecretType:

    def __init__(self, type, mode='r'):
        self.mode = mode
        self.type = type

    def __call__(self, file_name):
        with open(file_name, self.mode) as file:
            data = file.read().strip()

        return self.type(data)


@dataclass
class LogArgs:
    container_name: str
    tail_number: int = 5

    def __post_init__(self):
        self.tail_number = int(self.tail_number)


@dataclass
class ListArgs:
    limit: int = 15
    all: bool = False

    def __post_init__(self):
        if isinstance(self.all, bool):  # Default value, no input
            return

        str_to_bool = {'t': True,
                       'f': False} 
        self.all = str_to_bool[self.all.lower()]


@dataclass
class StartStopArgs:
    container_name: str
    timeout: int = 10

    def __post_init__(self):
        self.timeout = int(self.timeout)


@dataclass
class EchoArgs:
    text: str = None


class ArgsHandler(CommandHandler):

    def __init__(self, command, callback, args_class, **kwargs):
        wrapped_cbk = self._wrapp_callback(command, callback, args_class)
        super().__init__(command, wrapped_cbk, **kwargs)
     
    def _wrapp_callback(self, command, callback, args_class):

        async def wrapped_callback(update, context):
            logging.info('Command %s', update.message.text)

            try:
                cbk_args = args_class(*context.args)
            except Exception:
                error = f'Wrong arguments of {args_class}'
                logging.error(error)
                await context.bot.send_message(chat_id=update.effective_chat.id, text=error)
                return

            return await callback(update, context, cbk_args)

        return wrapped_callback


def reg_command(name, description, args=None, scopes=(BotCommandScopeChat, )):
    if args:
        description += ' ' + 'Params: ' + field_names(args)
        
    cmd = BotCommand(name, description)
    bot_cmd_ctx.set(name)
    handler_args_ctx.set(args)
    
    for scope in scopes:
        SCOPES[scope].append(cmd)        
       
    def decorator(func):
        
        @wraps(func)
        async def wrapper(*args, **kwargs):    
            result = await func(*args, **kwargs)
            return result
        
        return wrapper
    
    return decorator


def reg_handler(handler_class):
    
    def decorator(func):
        bot_cmd = bot_cmd_ctx.get()  # The command is set above in its decorator chain
        handler_args = handler_args_ctx.get()
        class_args = [bot_cmd, func]
        if handler_args:
            class_args.append(handler_args)
        
        handler = handler_class(*class_args)
        HANDLERS[handler_class].append(handler)
        
        @wraps(func)
        async def wrapper(*args, **kwargs):
            result = await func(*args, **kwargs)
            return result

        return wrapper

    return decorator


def reply_fabric(command, text) -> str:
    now = datetime.now(timezone.utc).isoformat(sep=' ', timespec='seconds')
    
    header = f'_command_ `{command}`'

    body = '\n\n'
    body += escape_markdown(text, version=2)
    body += '\n' if text.endswith('\n') else '\n\n'

    footer = f'_replied_ `{now}`'

    reply = header + body + footer
    return reply


@reg_command('info', 'User and chat information', scopes=(BotCommandScopeDefault, BotCommandScopeChat))
@reg_handler(CommandHandler)
async def user_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = f'{update.effective_user} \n {update.effective_chat}'
    reply = reply_fabric(update.message.text, text)
    await context.bot.send_message(chat_id=update.effective_chat.id, text=reply,
                                   parse_mode=ParseMode.MARKDOWN_V2)

    
@reg_command('echo', 'Resend you typed text or echoing.', args=EchoArgs)
@reg_handler(ArgsHandler)
@inject.autoparams()
async def echo(update: Update, context: ContextTypes.DEFAULT_TYPE, args: EchoArgs, docker_client: DockerClient):
    text = 'echo' if args.text is None else args.text
    reply = reply_fabric(update.message.text, text)
    await context.bot.send_message(chat_id=update.effective_chat.id, text=reply,
                                   parse_mode=ParseMode.MARKDOWN_V2)


@reg_command('list', 'List containers.', args=ListArgs)
@reg_handler(ArgsHandler)
@inject.autoparams()
async def list_containers(update: Update, context: ContextTypes.DEFAULT_TYPE, args: ListArgs, docker_client: DockerClient):
    c_list = docker_client.containers.list(all=args.all, limit=args.limit)

    text = ''
    for c in c_list:
        text += f'{c.name} {c.status}\n'

    reply = reply_fabric(update.message.text, text)
    await context.bot.send_message(chat_id=update.effective_chat.id, text=reply,
                                   parse_mode=ParseMode.MARKDOWN_V2)


@reg_command('logs', 'Return logs of a container.', args=LogArgs)
@reg_handler(ArgsHandler)
@inject.autoparams()
async def get_container_logs(update: Update, context: ContextTypes.DEFAULT_TYPE, args: LogArgs, docker_client: DockerClient):
    c = docker_client.containers.get(args.container_name)

    logs = c.logs(tail=args.tail_number).decode()
    
    reply = reply_fabric(update.message.text, logs)
    await context.bot.send_message(chat_id=update.effective_chat.id, text=reply,
                                   parse_mode=ParseMode.MARKDOWN_V2)


@reg_command('restart', 'Restart a container.', args=StartStopArgs)
@reg_handler(ArgsHandler)
@inject.autoparams()
async def restart_container(update: Update, context: ContextTypes.DEFAULT_TYPE, args: StartStopArgs, docker_client: DockerClient):
    c = docker_client.containers.get(args.container_name)
    
    reply = reply_fabric(update.message.text, 'restarting...')
    await context.bot.send_message(chat_id=update.effective_chat.id, text=reply,
                                   parse_mode=ParseMode.MARKDOWN_V2)

    # At the end to avoid cycling restarts
    async def restart():
        c.restart(timeout=args.timeout)
        reply = reply_fabric(update.message.text, 'restarted.')
        await context.bot.send_message(chat_id=update.effective_chat.id, text=reply,
                                       parse_mode=ParseMode.MARKDOWN_V2)
    context.application.create_task(restart())


@reg_command('stop', 'Stop a container.', args=StartStopArgs)
@reg_handler(ArgsHandler)
@inject.autoparams()
async def stop_container(update: Update, context: ContextTypes.DEFAULT_TYPE, args: StartStopArgs, docker_client: DockerClient):
    c = docker_client.containers.get(args.container_name)

    reply = reply_fabric(update.message.text, 'stopping...')
    await context.bot.send_message(chat_id=update.effective_chat.id, text=reply,
                                   parse_mode=ParseMode.MARKDOWN_V2)

    # At the end to avoid cycling restarts
    async def stop():
        c.stop(timeout=args.timeout)
        reply = reply_fabric(update.message.text, 'stopped.')
        await context.bot.send_message(chat_id=update.effective_chat.id, text=reply,
                                       parse_mode=ParseMode.MARKDOWN_V2)
    context.application.create_task(stop())


@reg_command('start', 'Star a stopped container.', args=StartStopArgs)
@reg_handler(ArgsHandler)
@inject.autoparams()
async def start_container(update: Update, context: ContextTypes.DEFAULT_TYPE, args: StartStopArgs, docker_client: DockerClient):
    c = docker_client.containers.get(args.container_name)

    c.start()
    reply = reply_fabric(update.message.text, 'started')
    await context.bot.send_message(chat_id=update.effective_chat.id, text=reply, parse_mode=ParseMode.MARKDOWN_V2)


async def set_commands(scope: BotCommandScopeDefault | BotCommandScopeChat,
                       commands: list[BotCommand],
                       application: Application):
    await application.bot.set_my_commands(commands, scope=scope)

    cmd_names = ', '.join(bc.command for bc in commands)
    logging.info('Commands have been set: %s[%s]', scope.__class__.__name__, cmd_names)

     
async def error_handler(update: Update, context):
        logging.exception('Undefined error')
        
        reply = reply_fabric(update.message.text, str(context.error))
        await context.bot.send_message(chat_id=update.effective_chat.id, text=reply, parse_mode=ParseMode.MARKDOWN_V2)


if __name__ == '__main__':
    parser = ArgumentParser()
    parser.add_argument('token', type=FileSecretType(str), help='Absolute path to a text file with a bot token in.')
    parser.add_argument('user_id', type=FileSecretType(int), help='Absolute path to a text file with an allowed user id number.')
    args = parser.parse_args()

    inject.configure(lambda binder: binder.bind_to_provider(DockerClient, from_env))    
    
    async def set_commands_init(application: Application):
        await set_commands(BotCommandScopeDefault(), SCOPES[BotCommandScopeDefault], application)
        await set_commands(BotCommandScopeChat(args.user_id), SCOPES[BotCommandScopeChat], application)
        
    application = ApplicationBuilder() \
                  .token(args.token) \
                  .post_init(set_commands_init) \
                  .build()
    application.add_error_handler(error_handler)

    # Make all the Docker handlers private
    for handler in HANDLERS[ArgsHandler]:
        handler: ArgsHandler
        handler.filters = filters.User(user_id=args.user_id)
        
    handlers = list(chain.from_iterable(HANDLERS.values()))
    application.add_handlers(handlers)
    application.run_polling(allowed_updates=['message'], drop_pending_updates=True)
