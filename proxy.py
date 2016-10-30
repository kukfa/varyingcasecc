#!/usr/bin/env python3

import bitarray
import email
import email.policy
import os
import socket
import struct
import sys
import threading

maxRecvSize = 4096
browserEncScheme = 'utf-8'
proxyEncScheme = 'utf-8'
policy = email.policy.compat32.clone(linesep='\r\n')
otherIP = None
otherPort = None
serverMode = False

#TODO handle exceptions gracefully

'''
Logic for the browser-side proxy
'''
def processBrowser(conn, client):
    # get message and convert it to bit representation
    message = input("Enter message to send: ")
    bits = bitarray.bitarray()
    bits.frombytes(message.encode(proxyEncScheme))

    try:
        # open connection to other proxy
        sOther = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sOther.connect((otherIP, otherPort))

        # loop until entire covert message is sent
        messageSent = False
        while not messageSent:
            # receive request from browser
            request = conn.recv(maxRecvSize).decode(browserEncScheme)

            # insert covert message, determine if message incomplete
            modifiedReq, messageSent = modifyCase(request, bits)
            if not messageSent:
                print(str(bits.length()) + " bits remaining;" +
                        " please send additional request.")

            # send modified request to other proxy
            sOther.send(modifiedReq.encode(proxyEncScheme))

            responseBits = bitarray.bitarray()
            # loop until entire covert message received
            eofFound = False
            while not (eofFound):
                # receive response from other proxy
                response = sOther.recv(maxRecvSize)
                top, crlf, body = response.partition(b'\x0D\x0A\x0D\x0A')
                top = top.decode(proxyEncScheme)

                # extract covert message, determine if message incomplete
                eofFound = interpretCase(top, responseBits)

                if (eofFound):
                    for i in range(responseBits.length() % 8):
                        responseBits.pop()
                    if (responseBits.length() > 0):
                        responseBits.bytereverse()
                        recvMsg = responseBits.tobytes().decode(proxyEncScheme)[::-1]
                        print("Received message: " + recvMsg)

                # forward the message to the browser
                response = top.encode(browserEncScheme) + body
                conn.send(response)

        sOther.close()
        conn.close()
    except socket.error as err:
        print("Error connecting to other proxy: " + str(err))
        conn.close()


'''
Logic for the server-side proxy
'''
def processServer(conn, client):
    eofFound = False
    bits = bitarray.bitarray()
    while not (eofFound):
        # receive request with covert message
        modifiedReq = conn.recv(maxRecvSize).decode(proxyEncScheme)

        # extract the covert message
        eofFound = interpretCase(modifiedReq, bits)

        # determine intended web server
        try:
            webSrv, webPort = determineWebSrv(modifiedReq)
            sWeb = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sWeb.connect((webSrv, webPort))

            # forward request to web server
            sWeb.send(modifiedReq.encode(browserEncScheme))

            # receive the response
            response = sWeb.recv(maxRecvSize)
            headers, crlf, body = response.partition(b'\x0D\x0A\x0D\x0A')
            headers = headers.decode(browserEncScheme)

            if (eofFound):
                for i in range(bits.length() % 8):
                    bits.pop()
                bits.bytereverse()
                recvMsg = bits.tobytes().decode(proxyEncScheme)[::-1]
                print("Received message: " + recvMsg)

                # get message and convert it to bit representation
                message = input("Enter message to send: ")
                sendBits = bitarray.bitarray()
                sendBits.frombytes(message.encode(proxyEncScheme))

                # use this response to send entire covert message
                messageSent = False
                while not messageSent:
                    # insert covert message, determine if more requests needed
                    modHeaders, messageSent = modifyCase(headers, sendBits)

                    # send modified response to other proxy
                    modResp = modHeaders.encode(proxyEncScheme) + body
                    conn.send(modResp)
            else:
                conn.send(response)
            sWeb.close()
        except KeyError as err:
            print(str(err))
            sys.exit(1)
        except socket.error as err:
            print("Error connecting to web server: " + str(err))
            sys.exit(1)


def interpretCase(modifiedReq, bits):
    requestLine, headers = extractHeaders(modifiedReq)
    tuples = headers.items()

    eofFound = False
    for header, value in tuples:
        chars = list(header)

        for char in chars:
            if (char.islower()):
                bits.append(False)
            elif (char.isupper()):
                bits.append(True)

        if (value.endswith('  ')):
            eofFound = True
            break

    return eofFound


def modifyCase(request, bits):
    # get header-value tuples
    requestLine, headers = extractHeaders(request)
    tuples = headers.items()

    messageSent = False
    # modify the case of each header
    for i in range(len(tuples)):
        header, value = tuples[i]
        chars = list(header)

        for j in range(len(chars)):
            if (chars[j].isalpha()):
                try:
                    bit = bits.pop()
                except IndexError:
                    # append EOF indicator
                    value += '  '
                    break

                if (bit == True):
                    chars[j] = chars[j].upper()
                else:
                    chars[j] = chars[j].lower()

        del headers[header]
        newHeader = ''.join(chars)
        headers[newHeader] = value

        if (len(bits) == 0):
            messageSent = True

    # rebuild the request
    newHeaders = headers.as_string()
    newRequest = requestLine + '\r\n' + newHeaders

    return newRequest, messageSent


def determineWebSrv(request):
    requestLine, headers = extractHeaders(request)

    for header, value in headers.items():
        if (header.lower() == 'host'):
            hostAndPort = value.split(':')
            if (len(hostAndPort) == 2):
                return socket.gethostbyname(hostAndPort[0]), hostAndPort[1]
            else:
                return socket.gethostbyname(value), 80

    raise KeyError('Cannot determine intended host')


def extractHeaders(request):
    requestLine, headers = request.split('\r\n', 1)
    headers = email.message_from_string(headers, policy=policy)
    return requestLine, headers


#def extractMessage TODO implement this?


def main():
    global serverMode, otherIP, otherPort

    # check argument length
    if (len(sys.argv) < 5):
        print("Usage: " + sys.argv[0] + " <client/server mode> <port>" + 
            " <other proxy IP> <other proxy port>")
        sys.exit(1)

    # process arguments
    role = sys.argv[1]
    if (role.lower() == "server"):
        serverMode = True
    listPort = int(sys.argv[2])
    otherIP = sys.argv[3]
    otherPort = int(sys.argv[4])

    # open listening socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(('', listPort))
        s.listen(1)

        # handle incoming connections
        while True:
            conn, client = s.accept()
            if (serverMode):
                t = threading.Thread(target=processServer, args=(conn, client))
            else:
                t = threading.Thread(target=processBrowser, args=(conn, client))
            t.start()
    except socket.error as err:
        print("Error opening socket: " + err)
    except KeyboardInterrupt:
        pass

    s.close()


if __name__ == '__main__':
    main()
