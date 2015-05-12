import socket
import enum
import os
import stat
import pwd
import grp
import threading
import sqlite3

COMMAND_PORT = 5006


class FtpMode(enum.Enum):
    ACTIVE = 1
    PASSIVE = 2


class TransferType(enum.Enum):
    ASCII = 1
    BINARY = 2


class ServerState(enum.Enum):
    WAITING_FOR_LOGIN = 1
    WAITING_FOR_PASSWORD = 2
    LOGGED_IN = 3


class FtpRequest(threading.Thread):

    def __init__(self, connection, client_id):
        threading.Thread.__init__(self)
        self.command_connection = connection
        self.client_id = client_id

        self.server_socket = None
        self.data_socket = None

        self.remote_host = ''
        self.remote_port = 0

        self.command = ''
        self.parameter = ''
        self.reply = ''
        self.user = ''
        self.current_directory = FtpServer.initDir
        self.root_directory = '/'

        self.server_state = ServerState.WAITING_FOR_LOGIN
        self.ftp_mode = FtpMode.ACTIVE
        self.transfer_type = TransferType.BINARY

    def parse_input(self, input_string):
        space_position = str(input_string).find(' ')
        print(len(input_string))
        if space_position == -1:
            self.command = input_string
        else:
            self.command = input_string[:space_position]

        if not (space_position >= len(input_string) or space_position == -1):
            self.parameter = input_string[space_position + 1:]

        return self.command

    def run(self):
        quited = False
        while not quited:
            line = ''
            while not line.endswith('\n'):
                line += (self.command_connection.recv(100)).decode('utf-8')

            line = line.strip()
            if line == '':
                return

            request_command = self.parse_input(line)
            print(request_command)

            if self.server_state == ServerState.WAITING_FOR_LOGIN:
                self.perform_user()
            elif self.server_state == ServerState.WAITING_FOR_PASSWORD:
                self.perform_pass()
            elif self.server_state == ServerState.LOGGED_IN:
                if request_command == 'CDUP':
                    self.perform_cdup()
                elif request_command == 'CWD':
                    self.perform_cwd()
                elif request_command == 'QUIT':
                    self.perform_quit()
                    quited = True
                elif request_command == 'RETR':
                    self.perform_retr()
                elif request_command == 'STOR':
                    self.perform_stor()
                elif request_command == 'LIST':
                    self.perform_list()
                elif request_command == 'PORT':
                    self.perform_port()
                elif request_command == 'TYPE':
                    self.perform_type()
                elif request_command == 'NOOP':
                    self.perform_noop()
                elif request_command == 'PASV':
                    self.perform_pasv()
                elif request_command == 'PWD':
                    self.perform_pwd()

            self.command_connection.send(bytes(self.reply, 'utf-8'))
        self.command_connection.close()

    def is_username_valid(self, username):
        DB_NAME = 'users.db'
        SELECT = 'SELECT name FROM users WHERE name="%s"' % username

        connection = sqlite3.connect(DB_NAME)
        cursor = connection.execute(SELECT)
        row = cursor.fetchone()
        connection.close()

        return row is not None

    def perform_user(self):
        if self.command == 'USER':
            if self.is_username_valid(self.parameter):
                self.reply = '331 user name okay, need password\r\n'
                self.server_state = ServerState.WAITING_FOR_PASSWORD
                self.user = self.parameter
            else:
                self.reply = '430 incorrect username\r\n'
        else:
            self.reply = '501 syntax error in parameters or arguments\r\n'

    def is_pass_valid(self, password):
        DB_NAME = 'users.db'
        SELECT = 'SELECT password FROM users WHERE name="%s"' % self.user

        connection = sqlite3.connect(DB_NAME)
        cursor = connection.execute(SELECT)
        row = cursor.fetchone()
        connection.close()

        if row is None:
            return False

        valid_password = row[0]

        if valid_password == password:
            return True
        else:
            return False

    def perform_pass(self):
        if self.command == 'PASS':
            if self.is_pass_valid(self.parameter):
                self.reply = '230 User logged in, proceed\r\n'
                self.server_state = ServerState.LOGGED_IN
            else:
                self.reply = '430 Invalid password\r\n'

        else:
            self.reply = '530 Not logged in\r\n'

    def perform_cdup(self):
        if self.current_directory != self.root_directory:
            self.current_directory = os.path.abspath(os.path.join(self.current_directory, os.pardir))
        self.reply = '200 ok\r\n'

        if not self.current_directory.endswith('/'):
            self.current_directory += '/'

    def perform_cwd(self):
        if not self.current_directory.endswith('/'):
            self.current_directory += '/'

        if os.path.exists(self.parameter) and os.path.isdir(self.parameter):
            if self.parameter == '..' or self.parameter == '..\\':
                if self.current_directory != self.root_directory:
                    self.current_directory = os.path.abspath(os.path.join(self.current_directory, os.pardir))
                self.reply = '200 ok\r\n'
            elif self.parameter == '.' or self.parameter == '.\\':
                pass
            else:
                self.current_directory = self.parameter
        elif (os.path.exists(self.current_directory + self.parameter)
              and os.path.isdir(self.current_directory + self.parameter)):
            self.current_directory += self.parameter
            self.reply = '250 ok\r\n'
        else:
            self.reply = '501 syntax error in parameters or arguments\r\n'

        if not self.current_directory.endswith('/'):
            self.current_directory += '/'

    def perform_quit(self):
        self.reply = '221 service closing connection\r\n'

    def perform_retr(self):
        if self.parameter == '':
            self.reply = '501 syntax error\r\n'
            return

        requested_file = self.current_directory + self.parameter

        self.command_connection.send(
            bytes('150 opening binary mode data connection for ' + requested_file + '\r\n', 'utf-8')
        )

        if self.ftp_mode == FtpMode.ACTIVE:
            self.data_socket = socket.socket()
            self.data_socket.connect((self.remote_host, self.remote_port))
        elif self.ftp_mode == FtpMode.PASSIVE:
            self.data_socket, addr = self.server_socket.accept()

        if self.transfer_type == TransferType.BINARY:
            content = open(requested_file, 'rb').read()
            self.data_socket.send(content)
            self.reply = '226 file transfer finished\r\n'
        elif self.transfer_type == TransferType.ASCII:
            content = open(requested_file, 'rt').read()
            self.data_socket.send(bytes(content, 'utf-8'))
            self.reply = '226 file transfer finished\r\n'

        self.data_socket.close()

    def perform_stor(self):
        if self.parameter == '':
            self.reply = '501 syntax error\r\n'
            return

        stored_file = self.current_directory + self.parameter

        self.command_connection.send(
            bytes('150 Opening data connection for ' + stored_file + '\r\n', 'utf-8')
        )

        if self.ftp_mode == FtpMode.ACTIVE:
            self.data_socket = socket.socket()
            self.data_socket.connect((self.remote_host, self.remote_port))
        elif self.ftp_mode == FtpMode.PASSIVE:
            self.data_socket, addr = self.server_socket.accept()

        if self.transfer_type == TransferType.BINARY:
            file = open(stored_file, 'wb+')
            data = b' '
            while len(data) != 0:
                data = self.data_socket.recv(4096)
                file.write(data)
            file.close()
        elif self.transfer_type == TransferType.ASCII:
            file = open(stored_file, 'wt+')
            data = ' '
            while len(data) != 0:
                data = self.data_socket.recv(4096)
                data = data.decode('UTF-8')
                file.write(data)
            file.close()

        self.reply = '226 transfer complete\r\n'

    def perform_pwd(self):
        self.reply = '257 "' + self.current_directory + '"\r\n'

    def perform_list(self):
        if self.ftp_mode == FtpMode.ACTIVE:
            if self.remote_host == '' or self.remote_port == 0:
                self.reply = '503 bad sequence of commands\r\n'
                return
            self.data_socket = socket.socket()
            self.data_socket.connect((self.remote_host, self.remote_port))
        elif self.ftp_mode == FtpMode.PASSIVE:
            self.data_socket, addr = self.server_socket.accept()

        self.command_connection.send(bytes('125 data connection already open\r\n', 'utf-8'))
        if not self.current_directory.endswith('/'):
                    self.current_directory += '/'

        try:
            for i in os.listdir(self.current_directory):
                permission = stat.filemode(os.stat(self.current_directory + i).st_mode)
                stat_info = os.stat(self.current_directory + i)
                uid = stat_info.st_uid
                gid = stat_info.st_gid
                user = pwd.getpwuid(uid)[0]
                group = grp.getgrgid(gid)[0]
                file_size = os.stat(self.current_directory + i).st_size

                self.data_socket.send(bytes(permission + ' 1 ' + user + " " + group + " " + str(file_size) + " " + i + '\r\n', 'utf-8'))
        except PermissionError:
            pass
        except FileNotFoundError:
            pass

        self.data_socket.close()

        self.reply = '226 transfer complete\r\n'

    def perform_port(self):
        address_parts = []
        parts_amount = 6
        start_position = 0

        for i in range(0, parts_amount - 1):
            end_position = self.parameter.find(',', start_position)
            address_parts.append(self.parameter[start_position:end_position])
            start_position = end_position + 1
        address_parts.append(self.parameter[start_position:])

        self.remote_host = address_parts[0] + '.' + address_parts[1] + '.' \
            + address_parts[2] + '.' + address_parts[3]
        self.remote_port = int(address_parts[4]) * 256 + int(address_parts[5])
        print(int(address_parts[4]) * 256 + int(address_parts[5]))

        self.ftp_mode = FtpMode.ACTIVE
        self.reply = '200 \r\n'

    def perform_type(self):
        if self.parameter == 'A':
            self.transfer_type = TransferType.ASCII
            self.reply = '200 change to ascii mode\r\n'
        elif self.parameter == 'I':
            self.transfer_type = TransferType.BINARY
            self.reply = '200 change to binary mode\r\n'
        else:
            self.reply = '504 command not implemented\r\n'

    def perform_noop(self):
        self.reply = '200 ok'

    def perform_pasv(self):
        if self.server_socket is not None:
            self.server_socket.close()

        self.server_socket = socket.socket()
        self.server_socket.bind(('127.0.0.1', 0))
        data_port = self.server_socket.getsockname()[1]
        self.server_socket.listen(10)
        self.reply = '227 entering passive mode(127,0,0,1,%d,%d)\r\n' % (int(data_port / 256), data_port % 256)
        self.ftp_mode = FtpMode.PASSIVE

class FtpServer:
    counter = 0
    initDir = '/'
    users = []
    usersInfo = []

    def __init__(self):
        counter = 1
        client_id = 0
        command_socket = socket.socket()
        command_socket.bind(('127.0.0.1', COMMAND_PORT))
        command_socket.listen(10)

        while True:
            connection, addr = command_socket.accept()
            connection.send(bytes('220 service ready for new user, ' + str(counter) + '\r\n', 'utf-8'))
            ftp_request = FtpRequest(connection, client_id)
            print("few")
            ftp_request.start()

            FtpServer.users.append(ftp_request)
            counter += 1
            client_id += 1

if __name__ == '__main__':
    ftp_server = FtpServer()