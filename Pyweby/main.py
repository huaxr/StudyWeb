#coding:utf-8

import time
import os
from handle.request import HttpRequest

from core.router import  Looper
from core.concurrent import Executor, asyncpool
from handle.response import  cache_result
from common.wrapper import restful
from handle.auth import login_require
from common.compat import COUNT
from common.config import Configs
from poll.app import Application
from core.orm_engine import IntegerField, StringField, Model

class User(Model):
    id = IntegerField("id", auto_increment=True)
    user = StringField("user", not_null=True, default="null")
    passwd = StringField("passwd", not_null=True, default="null")
    privilege = StringField("privilege", not_null=True, default="R")
    information = StringField("information", not_null=True, default="R")
    # TODO not ok set here
    PRIMARYKEY = 'id'


class sessions(Model):
    session = StringField("session", not_null=True)
    value = StringField("value", not_null=True)


class testRouter(HttpRequest):
    def post(self):
        return self.get_json()

    def get(self):
        #return self.redirect("/test/?key=2")
        self.raise_status(401, "sorry, you are not allowd")
        # return self.render("upload.html", name=[1, 2, 3], ignore_cache=False)
        # c = User.get(user='hua').exclude(passwd='123').commit()
        # for i in c:
        #     print(i)
        # a = self.ghost(['a','b','c'])
        #self.set_headers({"Connection":"Keep-Alive"})


class testRouter2(HttpRequest):
    executor = Executor(COUNT)

    @asyncpool(executor=executor)
    def sleeper(self, counts):
        time.sleep(counts)
        return "sleeper call over, %d" %(counts)

    def get(self):
        arguments = self.get_arguments('key', 'defalut')
        try:
            value = int(arguments)
        except Exception:
            value = 1
        result = self.sleeper(value)
        return result, 200

    @restful  # test from restful api
    def post(self):
        return "xxx"   # form support


class cookie(HttpRequest):

    @restful           # use restful before the cache_result !
    @cache_result(expiration=60)
    def post(self):
        time.sleep(5)
        return "Hello World",200

    def get(self):
        yy = self.get_cookie()
        return yy


@login_require
class admin(HttpRequest):
    def get(self):
        user =  self.current_user or self.request.current_user()
        if user:
            return "admin user %s, your priv is %s" %(user.name,user.can_upload)
        else:
            self.set_header("xxxxx", "yyy")
            self.raise_status(401)

    def post(self):
        user = self.current_user
        if user.can_upload:
            filename = self.file.filename
            print(filename)
            self.file.saveto("C:\\"+filename)
            return "success"
        else:
            return "sorry , only is_admin can upload files"


class register(HttpRequest):
    def get(self):
        name = self.get_arguments('name', '')
        passwd = self.get_arguments('passwd', '')
        level = self.get_arguments('level', 'R').upper()
        with User(id=0,user=name,passwd=passwd,privilege=level,information={'sign':'never give up','nickname':'华'}) as u:
            u.save()
        return "register ok. please login."


class logout(HttpRequest):
    def get(self):
        self.clear_cookie()
        return "clean ok"


class login(HttpRequest):
    def get(self):
        name = self.get_arguments('name', '')
        passwd = self.get_arguments('passwd', '')
        user =  User.get(user=name,passwd=passwd).fetchone()

        if user and self.can_read(user):
            self.set_cookie({'name':name,'level':self.user_priv_dict(user)})
            return "%s login ok" %name + self.user_priv_dict(user)
        # user.is_admin()


class Barrel(Application):
    def __init__(self):
        self.handlers = [(r'/test/',testRouter2),
                         (r'/xx', testRouter),
                         (r'/cookie', cookie),
                         (r'/admin', admin),
                         (r'/register', register),
                         (r'/logout', logout),
                         (r'/login', login),]

        self.settings = {
            "ssl_options": {"ssl_enable": 0,
                            # TODO SSL with python2.7 env will reach ssl.Error PEM lib (_ssl.c:2693) Solved :2018.9.6
                            "ssl_version": Configs.V23,
                            "certfile": os.path.join(os.path.dirname(__file__), "static","server.crt"),
                            "keyfile": os.path.join(os.path.dirname(__file__), "static","server.key")},

            "template_path" : os.path.join(os.path.dirname(__file__), "templates"),
            "static_path" : os.path.join(os.path.dirname(__file__), "static"),
            "safe_cookie" : 'YnicJQBLgFAbAaP_nUJTHMA3Eq-G9WpNeREQL-qljLE=',
            "uri_prefix": "",
        }
        super(Barrel, self).__init__(self.handlers,self.settings)

    def before_request(self):
        pass

    def after_request(self):
        pass


if __name__ == '__main__':
    loop = Looper()
    server = loop(Barrel) or loop(handlers=[(r'/hello', testRouter2), ], enable_manager=1)
    server.listen(8738)
    server.server_forever(debug=False)


