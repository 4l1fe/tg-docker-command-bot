import logging
from dataclasses import dataclass
from argparse import ArgumentParser

import docker
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler


logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                    level=logging.INFO)


@dataclass
class LogArgs:
    container_name: str
    tail_number: int = 5

    def __post_init__(self):
        self.tail_number = int(self.tail_number)
    

class ArgsHandler(CommandHandler):

    def __init__(self, command, callback, args_class, **kwargs):
        wrapped_cbk = self._wrapp_callback(command, callback, args_class)
        super().__init__(command, wrapped_cbk, **kwargs)
     
    def _wrapp_callback(self, command, callback, args_class):

        async def wrapped_callback(update, context):
            logging.info('Command args: %s, %s', command, context.args)

            try:
                cbk_args = args_class(*context.args)
            except Exception:
                error = f'Wrong arguments of {args_class}'
                logging.error(error)
                await context.bot.send_message(chat_id=update.effective_chat.id, text=error)
                return

            return await callback(update, context, cbk_args)

        return wrapped_callback


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_message(chat_id=update.effective_chat.id, text="Succeett")


async def list_containers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    dcl = docker.from_env()
    c_list = dcl.containers.list(all=True)
    text = 'list\n\n'
    for c in c_list:
        text += f'{c.name} {c.status}\n'
    await context.bot.send_message(chat_id=update.effective_chat.id, text=text)


async def get_container_logs(update: Update, context: ContextTypes.DEFAULT_TYPE, args: LogArgs):
    dcl = docker.from_env()
    c = dcl.containers.get(args.container_name)
    logs = c.logs(tail=args.tail_number)
    logs = logs.decode()
    await context.bot.send_message(chat_id=update.effective_chat.id, text=logs)


async def restart_container(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pass


if __name__ == '__main__':
    parser = ArgumentParser()
    parser.add_argument('token', help='Absolute path to a text file with a bot token in.')
    args = parser.parse_args()
    
    with open(args.token, 'r') as file:
        token = file.read().strip()
        
    application = ApplicationBuilder().token(token).build()
    
    application.add_handler(CommandHandler('start', start))
    application.add_handler(CommandHandler('list', list_containers))
    application.add_handler(ArgsHandler('logs', get_container_logs, LogArgs))
    
    application.run_polling()
