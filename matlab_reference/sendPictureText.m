function sendPictureText(File,subject,msg,attachments)
%% Variables
% Recipients
name = File.User.Name;
emailAddress = File.User.Email;
phone = File.User.Phone;
carrier = File.User.Carrier;

%% Greetings
if ~isempty(name)
    name_len = length(name);
    space_idx = strfind(name,' ');
    firstName = name(space_idx+1:name_len);
    recipient = firstName;
    msg_greeting = sprintf('Hello %s,',recipient);
else
    msg_greeting = 'Hello,';
end

%% Text Message
if ~isempty(phone) && ~isempty(carrier)
    startTime = tic;
    message = { ...
        msg_greeting,...
        msg,...
        '', ...
        '-The Neural Interfaces Lab'};
    carrier_fix = strrep(strrep(carrier,'-',''),'&','');
    switch carrier_fix
        case 'alltel'
            phone_use = [phone '@message.alltel.com'];
        case 'att'
            phone_use = [phone '@txt.att.net'];
        case 'boost'
            phone_use = [phone '@myboostmobile.com'];
        case 'cingular'
            phone_use = [phone '@cingularme.com'];
        case 'cingular2'
            phone_use = [phone '@mobile.mycingular.com'];
        case 'cricket'
            phone_use = [phone '@sms.mycricket.com'];
        case 'metropcs'
            phone_use = [phone '@mymetropcs.com'];
        case 'nextel'
            phone_use = [phone '@messaging.nextel.com'];
        case 'sprint'
            phone_use = [phone '@messaging.sprintpcs.com'];
        case 'tmobile'
            phone_use = [phone '@tmomail.net'];
        case 'tracfone'
            phone_use = [phone '@mmst5@tracfone.com'];
        case 'uscellular'
            phone_use = [phone '@email.uscc.net'];
        case 'verizon'
            phone_use = [phone '@vtext.com'];
        case 'virgin'
            phone_use = [phone,'@vmobl.com'];
    end
    try
        sendmail(emailAddress,subject,message,attachments);
        sendmail(phone_use,subject,message,attachments);
    catch err
        report = getReport(err);
        disp(report);
    end

    [endTime,unit] = getEndTime(startTime);
    fprintf('OK (%.2f %s)\n',endTime,unit);
end


end