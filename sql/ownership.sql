-- FICTIONAL component -> team mapping, invented for this challenge (the brief asks us to).
-- BGL: keyed on the `component` column.  OpenStack: keyed on the `service` column
-- (nova-api / nova-compute / nova-scheduler), because that is the unit a team plausibly owns.
INSERT INTO ownership VALUES
 ('bgl','KERNEL','Kernel & Runtime'),
 ('bgl','APP','Application Support'),
 ('bgl','HARDWARE','Hardware Reliability'),
 ('bgl','LINKCARD','Interconnect & Network'),
 ('bgl','SERV_NET','Interconnect & Network'),
 ('bgl','MMCS','Control Plane'),
 ('bgl','CMCS','Control Plane'),
 ('bgl','BGLMASTER','Control Plane'),
 ('bgl','DISCOVERY','Control Plane'),
 ('bgl','MONITOR','Monitoring & Diagnostics'),
 ('bgl','DIAGS','Monitoring & Diagnostics'),
 ('openstack','nova-api','API Platform'),
 ('openstack','nova-compute','Compute'),
 ('openstack','nova-scheduler','Scheduling');
