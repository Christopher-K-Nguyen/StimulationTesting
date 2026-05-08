function sendError( ...
    File, ...
    subject, ...
    msg, ...
    attachments, ...
    varargin)
%% Recipients
name = File.User.Name;
emailAddress = File.User.Email;
phone = File.User.Phone;
carrier = File.User.Carrier;

%% Message
if ~isempty(name)
    name_len = length(name);
    space_idx = strfind(name,' ');
    firstName = name(space_idx+1:name_len);
    recipient = firstName;
else
    % Email name
    atSign = find(emailAddress == '@');
    emailName_idx = atSign - 1;
    recipient = emailAddress(1:emailName_idx);
end

msg_greeting = sprintf('Hello %s,',recipient);
numOfVar = length(varargin);
switch numOfVar
    case 1
        endTime_s = varargin{1};
        endTime_min = endTime_s / 60;
        endTime_h = endTime_min / 60;
        if endTime_s < 60
            endTime = endTime_s;
            unit = 's';
        elseif endTime_min < 60
            endTime = endTime_min;
            unit = 'min';
        else
            endTime = endTime_h;
            unit = 'h';
        end
    case 2
        endTime = varargin{1};
        unit = varargin{2};
end
if numOfVar > 0
    msg_1 = sprintf('Experiment failed at %.2f %s.',endTime,unit);
else
    msg_1 = sprintf('Experiment failed on %s.',getDateTIme());
end
msg_2 = sprintf('%s',msg);
msg_3 = 'The file from the experiment is attached to this message.';
msg_closing = '-The Neural Interfaces Lab';

%% Text Message
if ~isempty(phone) && ~isempty(carrier)
    fprintf('Sending text message...');
    startTime = tic;
    message = { ...
        '', ...
        msg_greeting,...
        msg_1,...
        '', ...
        msg_2, ...
        '', ...
        msg_closing};
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
        sendmail(phone_use,subject,message);
    catch err
        report = getReport(err);
        disp(report);
    end

    [endTime,unit] = getEndTime(startTime);
    fprintf('OK (%.2f %s)\n',endTime,unit);
end

%% Email Message
if ~isempty(emailAddress)
    fprintf('Sending email message...');
    startTime = tic;
    try
        % Send
        if ~isempty(attachments)
            msg_email = { ...
                msg_greeting,...
                msg_1,...
                '', ...
                msg_2, ...
                '', ...
                msg_3,...
                '', ...
                msg_closing};
            try
                sendmail(emailAddress,subject,msg_email,attachments);
            catch
                isCellArr = iscell(attachments);
                isStringArr = isstring(attachments);
                if isCellArr || isStringArr
                    zip_tf = containsi(attachments,'zip');
                    attachments_new = attachments(zip_tf);
                else
                    attachments_new = attachments;
                end
                sendmail(emailAddress,subject,msg_email,attachments_new);
            end
        else
            message = { ...
                msg_greeting,...
                msg_1,...
                '', ...
                msg_2, ...
                '', ...
                msg_closing};
            sendmail(emailAddress,subject,message);
        end
        [endTime,unit] = getEndTime(startTime);
        fprintf('OK (%.2f %s)\n',endTime,unit);
    catch
        [endTime,unit] = getEndTime(startTime);
        fprintf('FAILED (%.2f %s)\n',endTime,unit);
    end

end

end