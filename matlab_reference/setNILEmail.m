function [] = setNILEmail()
%% Constants
NIL_EMAIL = 'neuralinterfaceslabutd@gmail.com';      % NIL email
NIL_PASSWORD = 'ykazepbomkmbouzj';                           % NIL password

%% Function
fprintf('Setting up email...');
startTime = tic;
setpref('Internet','SMTP_Server','smtp.gmail.com'); % set to gmail server
setpref('Internet','E_mail',NIL_EMAIL);              % set to NIL email
setpref('Internet','SMTP_Username',NIL_EMAIL);       % set server to NIL email
setpref('Internet','SMTP_Password',NIL_PASSWORD);    % set server to NIL password
props = java.lang.System.getProperties;
props.setProperty('mail.smtp.auth','true');
props.setProperty('mail.smtp.socketFactory.class', 'javax.net.ssl.SSLSocketFactory');
props.setProperty('mail.smtp.socketFactory.port','465');
[endTime,unit] = getEndTime(startTime);
fprintf('OK (%.2f %s)\n',endTime,unit);

end