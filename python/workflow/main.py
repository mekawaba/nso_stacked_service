# -*- mode: python; python-indent: 4 -*-
import ncs
from ncs.application import Service, PlanComponent
from ncs.dp import Action
import time


# ------------------------
# SERVICE CALLBACK EXAMPLE
# ------------------------
class ServiceCallbacks(Service):

    # The create() callback is invoked inside NCS FASTMAP and
    # must always exist.
    @Service.create
    def cb_create(self, tctx, root, service, proplist):
        self.log.info('Service create(service=', service._path, ')')

        # initialize plan
        myplan = PlanComponent(service, 'bgpwork', 'workflow:type')
        myplan.append_state('step1')
        myplan.append_state('verify_IF')
        myplan.append_state('step2')
        myplan.append_state('post_check')

        vars = ncs.template.Variables()
        vars.add('INST', service.name)
        vars.add('DEV1', service.dev1)
        vars.add('DEV2', service.dev2)
        vars.add('DEV1LOOP', service.dev1_loop)
        vars.add('DEV2LOOP', service.dev2_loop)
        vars.add('ASNUM', service.asnum)
        template = ncs.template.Template(service)
        template.apply('workflow-template-kickers', vars)

        if service.step1:
            myplan.set_reached('step1')
            template.apply('step1-makeloop', vars)
        else:
            service.verify_IF = False

        if service.verify_IF:
            myplan.set_reached('verify_IF')

        if service.step2:
            if service.verify_IF:
                myplan.set_reached('step2')
                template.apply('step2-setbgp', vars)
            else:
                raise ValueError('Verify IF before execution.')  
        else:
            service.post_check = False

        if service.post_check:
            myplan.set_reached('post_check')

    # The pre_modification() and post_modification() callbacks are optional,
    # and are invoked outside FASTMAP. pre_modification() is invoked before
    # create, update, or delete of the service, as indicated by the enum
    # ncs_service_operation op parameter. Conversely
    # post_modification() is invoked after create, update, or delete
    # of the service. These functions can be useful e.g. for
    # allocations that should be stored and existing also when the
    # service instance is removed.

    # @Service.pre_modification
    # def cb_pre_modification(self, tctx, op, kp, root, proplist):
    #     self.log.info('Service premod(service=', kp, ')')

    # @Service.post_modification
    # def cb_post_modification(self, tctx, op, kp, root, proplist):
    #     self.log.info('Service postmod(service=', kp, ')')


# ---------------------------------------------
# COMPONENT THREAD THAT WILL BE STARTED BY NCS.
# ---------------------------------------------
class Main(ncs.application.Application):
    def setup(self):
        # The application class sets up logging for us. It is accessible
        # through 'self.log' and is a ncs.log.Log instance.
        self.log.info('Main RUNNING')

        # Service callbacks require a registration for a 'service point',
        # as specified in the corresponding data model.
        #
        self.register_service('workflow-servicepoint', ServiceCallbacks)

        # If we registered any callback(s) above, the Application class
        # took care of creating a daemon (related to the service/action point).

        # When this setup method is finished, all registrations are
        # considered done and the application is 'started'.
        self.register_action('wf-checkBGP-point', WFCheckBGPAction)
        self.register_action('wf-pingLoopback-point', WFpingLoopbackAction)

    def teardown(self):
        # When the application is finished (which would happen if NCS went
        # down, packages were reloaded or some error occurred) this teardown
        # method will be called.

        self.log.info('Main FINISHED')

class WFCheckBGPAction(Action):
    @Action.action
    def cb_action(self, uinfo, name, kp, input, output):
        self.log.info('checkBGP Action called (service=)', kp, ')')
        #with ncs.maapi.single_read_trans(uinfo.username, uinfo.context) as trans:
        with ncs.maapi.single_read_trans('admin', 'python') as trans:
            # get root path
            root = ncs.maagic.get_root(trans, kp)
            service = ncs.maagic.cd(root, kp)

            maapi = ncs.maagic.get_maapi(root)

            # get addresses
            dev1 = service.dev1
            dev2 = service.dev2    
            dev1loop = service.dev1_loop
            dev2loop = service.dev2_loop

            self.log.info("dev1loop = ",dev1loop)
            self.log.info("dev2loop = ",dev2loop)

            count_result = 0

            count = 0
            while count < 5:
            # check BGP status
                if self.check_bgp(trans, dev1, dev2loop):
                    msg = f"BGP session to {dev2loop} is Established!"
                    maapi.prio_message('admin', '\n'+msg)
                    count_result += 1
                    break
                else:
                    msg = f"BGP session to {dev2loop} is down..."
                    maapi.prio_message('admin', '\n'+msg)
                    count += 1
                    time.sleep(5)
            output.dev1 = msg

            count = 0
            while count < 5:
                if self.check_bgp(trans, dev2, dev1loop):
                    msg = f"BGP session to {dev1loop} is Established!"
                    maapi.prio_message('admin', '\n'+msg+'\n')
                    count_result += 1
                    break
                else:
                    msg = f"BGP session to {dev1loop} is down..."
                    maapi.prio_message('admin', '\n'+msg+'\n')
                    count += 1
                    time.sleep(5)
            output.dev2 = msg

            if count_result == 2:
                output.result = True      

        if output.result:
            #self.log.info("output.result == True")
            #with ncs.maapi.single_write_trans(uinfo.username, uinfo.context) as trans:
            with ncs.maapi.single_write_trans('admin', 'python') as trans:
                # get root path
                root = ncs.maagic.get_root(trans, kp)
                service = ncs.maagic.cd(root, kp)

                #self.log.info(service.post_check)
                # trigger next stage
                service.post_check = True
                #self.log.info(service.post_check)
                trans.apply()

    def check_bgp(self, trans, dev_name, bgp_nbr_addr):
        root = ncs.maagic.get_root(trans)
        device = root.ncs__devices.device[dev_name]
        ret = False
        try:
            command = "show bgp neighbor brief"
            #self.log.info('command: ', command)
            live_input = device.live_status.cisco_ios_xr_stats__exec.any.get_input()
            live_input.args = [command]
            output = device.live_status.cisco_ios_xr_stats__exec.any(live_input)
            #self.log.info("bgp_status output: ", output)
            #self.log.info("bgp_nbr_addr: ", bgp_nbr_addr)

            # parse output
            for line in output.result.split("\n"):
                if len(line)>0:
                    # if line start with the number, the line is neighbor info
                    words = line.split(" ")
                    #self.log.info(words)
                    #self.log.info("words[0]: ", words[0])
                    if bgp_nbr_addr in words[0] and "Established" in words[-2]:
                        ret = True
                        self.log.info("BGP session to ", bgp_nbr_addr, "is Established!")

            if ret == False:
                self.log.info("BGP session to ", bgp_nbr_addr, "is down...")

        except Exception as e:
            self.log.info(dev_name, " command error: ", str(e))

        return ret
    

class WFpingLoopbackAction(Action):
    @Action.action
    def cb_action(self, uinfo, name, kp, input, output):
        self.log.info('PING Action called (service=)', kp, ')')
        #with ncs.maapi.single_read_trans(uinfo.username, uinfo.context) as trans:
        with ncs.maapi.single_read_trans('admin', 'python') as trans:
            # get root path
            root = ncs.maagic.get_root(trans, kp)
            service = ncs.maagic.cd(root, kp)

            maapi = ncs.maagic.get_maapi(root)

            # get addresses
            dev1 = service.dev1
            dev2 = service.dev2
            dev1loop = service.dev1_loop
            dev2loop = service.dev2_loop

            self.log.info("dev1loop = ",dev1loop)
            self.log.info("dev2loop = ",dev2loop)

            output.result = False
            count = 0

            # execute Ping
            if self.ping_Loopback(trans, dev1, dev2loop):
                #msg = "Ping to "+dev2loop+" succeeded!"
                msg = f"Ping to {dev2loop} succeeded!"
                maapi.prio_message('admin', '\n'+msg+'\n')
                count += 1
            else:
                msg = f"Ping to {dev2loop} failed!"
                maapi.prio_message('admin', '\n'+msg+'\n')
            #output.dev1 = msg

            if self.ping_Loopback(trans, dev2, dev1loop):
                msg = f"Ping to {dev1loop} succeeded!"
                maapi.prio_message('admin', '\n'+msg+'\n')
                count += 1
            else:
                msg = f"Ping to {dev1loop} failed!"
                maapi.prio_message('admin', '\n'+msg+'\n')
            #output.dev2 = msg


            if count == 2:
                output.result = True

        if output.result:
            #self.log.info("output.result == True")
            #with ncs.maapi.single_write_trans(uinfo.username, uinfo.context) as trans:
            with ncs.maapi.single_write_trans('admin', 'python') as trans:
                # get root path
                root = ncs.maagic.get_root(trans, kp)
                service = ncs.maagic.cd(root, kp)

                #self.log.info(service.verify_IF)
                # trigger next stage
                service.verify_IF = True
                #self.log.info(service.verify_IF)
                trans.apply()

    def ping_Loopback(self, trans, dev_name, loopaddr):
        root = ncs.maagic.get_root(trans)
        device = root.ncs__devices.device[dev_name]
        ret = False
        try:
            command = "ping "+loopaddr
            #self.log.info('command: ', command)
            live_input = device.live_status.cisco_ios_xr_stats__exec.any.get_input()
            live_input.args = [command]
            output = device.live_status.cisco_ios_xr_stats__exec.any(live_input)
            #self.log.info("bgp_status output: ", output)
            #self.log.info("loopaddr: ", loopaddr)

            # parse output
            for line in output.result.split("\n"):
                #self.log.info(line)
                if len(line)>0:
                    # if line start with the number, the line is neighbor info
                    if "!!" in line:
                        ret = True
                        self.log.info("ping from ", dev_name, " to ", loopaddr, " succeeded!")

        except Exception as e:
            self.log.info(dev_name, " command error: ", str(e))

        return ret
