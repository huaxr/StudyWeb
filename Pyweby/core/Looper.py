#coding:utf-8
try:
    import queue as Queue #Python3
except ModuleNotFoundError:
    import Queue

import logging
import threading
import socket
import types
import time
import json
import select

from .ServerSock import gen_serversock
from .config import Configs
from handle.request import WrapRequest,HttpRequest
from handle.response import WrapResponse
from handle.exc import NoRouterHandlers,FormatterError
from util.logger import Logger
from util.Observer import Event, EventManager

class MainCycle(object):

    application = None
    '''
    do not add application in __init__, because in the subclass
    PollCycle, self.application will return the value is setted 
    None, so define application here in cls.
    '''

    def __init__(self,flag=False):
        self.flag = flag

    @classmethod
    def checking_handlers(cls,handlers,conn):
        for uri, obj in handlers:
            assert callable(obj)
            # cls <class 'cycle_sock.PollCycle'>
            if isinstance(uri,(str,bytes)) and issubclass(obj,HttpRequest):
                continue
            else:
                raise FormatterError(uri=uri,obj=obj)



class PollCycle(MainCycle,Configs.ChooseSelector):

    '''
    MSG_QUEUE is aim for avoiding threading condition and simplify
    the threading.Lock acquire and release
    '''
    MSG_QUEUE = threading.local()
    MSG_QUEUE.pair = {}

    MSG_QUEUE.connection = {}
    MSG_QUEUE.request = {}
    MSG_QUEUE.response = {}
    EOL1 = b'\n\n'
    EOL2 = b'\n\r\n'

    def __init__(self,*args,**kwargs):
        self._impl = kwargs.get('__impl',None)
        assert self._impl is not None

        self.timeout = kwargs.get('timeout',3600)
        self._thread_ident = threading.get_ident()

        # the handlers of uri-obj pair . e.g. [('/',MainHandler),]
        # there is two ways to deliver the parameter
        # 1. using handler=[(),] directly
        # 2. using Configs.Application wrapper the handler in __init__
        # so, using trigger_handlers to get handlers for later usage.
        self.handlers = self.trigger_handlers(kwargs)
        self.Log = Logger(logger_name=__name__)
        self.Log.setLevel(logging.INFO)

        self.eventManager = None
        '''
        enable EventManager, which means getting callback future
        to be achieve soon.
        '''
        self.enable_manager = kwargs.get('enable_manager',0)
        if self.enable_manager:
            self.eventManager = EventManager()
            self.swich_eventmanager_on()

        self.__EPOLL = hasattr(select, 'epoll')

        Configs.ChooseSelector.__init__(self,flag=self.__EPOLL)


    def swich_eventmanager_on(self):
        #starting the  eventManager._thread  for calling _run_events
        #and getting the events from the manager's Queue, if the Queue.get()
        #returns future object, means we catch the add_done_callback future
        #in finished. remember pass the sock obj for send result.
        self.eventManager.start()

    def swich_eventmanager_off(self):
        self.eventManager.stop()


    def listen(self,port):
        self.server = gen_serversock(port)

        #AttributeError: 'SelectCycle' object has no attribute 'handlers'
        PollCycle.check(self.handlers, None)
        self.add_handler(self.server, Configs.M)

    @classmethod
    def check(cls,handlers,conn):

        if len(handlers) <= 0:
            raise NoRouterHandlers
        cls.checking_handlers(handlers,conn)

    @classmethod
    def consume(cls,message,debug=False):
        r = message
        while True:
            msg = yield r
            if debug:
                print("consume",msg)
            if not msg:
                return
            r = msg

    @classmethod
    def Coroutine(cls,gen, msg,debug=False):
        next(gen)
        if isinstance(gen,types.GeneratorType):
            while True:
                try:
                    result = gen.send(msg)
                    if debug:
                        print("coroutine",result)
                    gen.close()
                    return result
                except StopIteration:
                    return


    def server_forever_select(self,debug=False):
        # this is blocking select.select io
        while True:
            try:
                # there may some sockets means Exception and close by self,
                # so we should pass it checking
                fd_event_pair = self._impl.sock_handlers(self.timeout)
            except OSError:
                continue

            if debug:
                self.Log.info(fd_event_pair)
                time.sleep(1)

            for sock,event in fd_event_pair:
                if event & Configs.R:
                    if sock is self.server:

                        conn, address = sock.accept()

                        conn.setblocking(0)
                        '''
                        for convenience and no blocking program, we put the connection into inputs loop,
                        with the next cycling, this conn's fileno being ready condition, and can be append 
                        by readable list by select loops
                        '''

                        # trigger add_sock to change select.select(pool)'s pool, and change the listening
                        # event IO Pool
                        self._impl.add_sock(conn, Configs.R)
                        # define the sock:Queue pair for message
                        self.MSG_QUEUE.pair[conn] = Queue.Queue()

                    else:
                        try:
                            data = sock.recv(60000)
                            # self.Log.info(data)
                        except socket.error:
                            self.close(sock)
                            continue

                        '''
                        here is the request origin.
                        PollCycle is origin from SelectCycle, application will be registered 
                        at there
               
                        the define of the main.py has two ways:
                        1. server = loop(handlers=[(r'/hello',testRouter2),])
                        2. server = loop(Barrel) 
                        
                        if 2 is choosen to be the method, with a series of examinations,
                        self.application will be set the Barrel instance

                        '''
                        data = WrapRequest(data,lambda x:dict(x),handlers=self.handlers,
                                           application=self.application,sock=sock)

                        if data:
                            # for later usage, when the next Loop by the kernel select, that's
                            # will be reuse the data putting in it.
                            self.MSG_QUEUE.pair[sock].put(data)
                            self._impl.add_sock(sock,Configs.W)

                        else:
                            # if there is no data receive, that means socket has been disconnected
                            # so , let's remove it now!
                            self.close(sock)

                elif event & Configs.W:
                    try:
                        msg = self.MSG_QUEUE.pair[sock].get_nowait()

                    except Queue.Empty:
                        pass

                    else:
                        # consider future.result() calling and non-blocking , we should
                        # activate the EventManager, and trigger sock.send() later in the
                        # EventManager sub threads Looping.
                        writers = WrapResponse(msg,self.eventManager,sock,self)
                        body = writers.gen_body(prefix="\r\n\r\n")
                        if body:
                            try:
                                bodys = body.encode()
                            except Exception:
                                bodys = body

                            # employ curl could't deal with 302
                            # you should try firefox or chrome instead

                            sock.send(bodys)
                            # do not call sock.close, because the sock still in the select,
                            # for the next loop, it will raise ValueEror `fd must not -1`
                            self.close(sock)
                        else:
                            '''
                            Question: maybe body is None, just close it means connection terminate.
                            and client will receive `Empty reply from server`?
                            
                            Answer:  No. if exception happens, or Future is waiting for 
                            the result() in the EventMaster, if you close the sock, you
                            will never send back message from the future!
                            Keep an eye on it!
                            '''
                            # self.Log.info(body)
                            # self.close(sock)
                            # we can't set None to it
                            continue
                else:
                    self.Log.info("other reason, close sock")
                    self.close(sock)

    def server_forever_epoll(self, debug=False,timeout=-1):
        '''
        Enhance the performance of IO loop.
        Using epoll first if condition promise.
        STEPS:
        1. Create an epoll object
        2. Tell the epoll object to monitor specific events on specific sockets
        3. Ask the epoll object which sockets may have had the specified event since the last query
        4. Perform some action on those sockets
        5. Tell the epoll object to modify the list of sockets and/or events to monitor
        6. Repeat steps 3 through 5 until finished
        7. Destroy the epoll object
        '''

        try:
            while 1:
                events = self._impl.poll(timeout)

                if not events:
                    self.Log.info("Epoll timeout, continue roll poling.")
                    continue

                if debug:
                    self.Log.info("there are %d events prepare to handling." %len(events))

                for fileno, event in events:
                    sock = self.MSG_QUEUE.connection[fileno]

                    # If active socket is the current server socket, represent that is a new connection.
                    # self.server if registered by listen method
                    if fileno == self.server.fileno():
                        conn, addr = self.server.accept()
                        conn.setblocking(0)
                        # Register new connection fileno to read event collection

                        self._impl.register(conn.fileno(), select.EPOLLIN)
                        self.MSG_QUEUE.connection[conn.fileno()] = conn
                        self.MSG_QUEUE.pair[conn] = Queue.Queue()

                    # Readable Rvent
                    elif event & select.EPOLLIN:

                        try:
                            data = sock.recv(60000)
                        except Exception as e:
                            self.Log.info(e)
                            self.eclose(fileno)

                        if self.application:
                            data = WrapRequest(data,lambda x:dict(x),handlers=self.handlers,
                                               application=self.application)

                        if data:
                            self.MSG_QUEUE.pair[sock].put(data)
                            self._impl.modify(fileno, select.EPOLLOUT)

                        else:
                            self.eclose(fileno)

                    # Writeable Event
                    elif event & select.EPOLLOUT:
                        try:
                            msg = self.MSG_QUEUE.pair[sock].get_nowait()

                            writers = WrapResponse(msg, self.eventManager, sock, self)
                            body = writers.gen_body(prefix="\r\n\r\n")

                        except Queue.Empty:
                            self._impl.modify(fileno, select.EPOLLIN)
                        else:
                            if body:
                                try:
                                    bodys = body.encode()
                                except Exception:
                                    bodys = body
                                finally:
                                    sock.send(bodys)
                                    self.close(sock)
                            else:
                                self.eclose(fileno)

                    # Close Event
                    elif event & select.EPOLLHUP:
                        if debug:
                            self.Log.info("Closing event,%d" %fileno)

                        self.eclose(fileno)

                    else:
                        continue

        except Exception as e:
            self.Log.info(e)
            self._impl.unregister(self.server.fileno())
            self._impl.close()
            self.server.close()

    def add_handler(self, fd, events):
        '''
        register sock fileno:events pair
        '''
        if self.__EPOLL:
            self._impl.register(fd, select.EPOLLIN)  # read
        else:
            self._impl.add_sock(fd, events)

    def split_fd(self, fd):
        try:
            return fd.fileno(), fd
        except AttributeError:
            return fd, fd

    def close(self,sock):
        assert isinstance(sock,socket.socket)
        self._impl.remove_sock(sock)
        sock.close()
        try:
            del self.MSG_QUEUE.pair[sock]
        except (AttributeError,KeyError):
            pass

    def eclose(self,fd):
        assert isinstance(fd,int) and fd>0
        self._impl.unregister(fd)
        sock = self.MSG_QUEUE.connection.pop(fd)
        sock.close()


    def trigger_handlers(self,kw):
        raise NotImplementedError
