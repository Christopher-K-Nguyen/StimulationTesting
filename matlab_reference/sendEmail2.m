function sendEmail2( ...
    emailAddress, ...
    emailSubject, ...
    attachments, ...
    varargin)
%% Function
if ~isempty(emailAddress)
    fprintf('Sending email...');
    startTime = tic;

    % Email name
    atSign = find(emailAddress == '@');
    emailName_idx = atSign - 1;
    emailName = emailAddress(1:emailName_idx);

    % Email message
    emailMsg_greeting = sprintf('Hello %s,',emailName);
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
    emailMsg_1 = sprintf('Experiment completed at %.2f %s.',endTime,unit);
    else
        emailMsg_1 = sprintf('Experiment completed on %s.',getDateTIme());
    end
    emailMsg_2 = 'The file(s) from the experiment is attached to this message.';
    emailMsg_closing = '-The Neural Interfaces Lab';

    % Send
    if ~isempty(attachments)
        emailMessage = { ...
            emailMsg_greeting,...
            emailMsg_1,...
            emailMsg_2,...
            '', ...
            emailMsg_closing};
        try
            sendmail(emailAddress,emailSubject,emailMessage,attachments);
        catch
            isCellArr = iscell(attachments);
            isStringArr = isstring(attachments);
            if isCellArr || isStringArr
                zip_tf = containsi(attachments,'zip');
                attachments_new = attachments(zip_tf);
            else
                attachments_new = attachments;
            end
            sendmail(emailAddress,emailSubject,emailMessage,attachments_new);
        end
    else
        emailMessage = { ...
            emailMsg_greeting,...
            emailMsg_1,...
            '', ...
            emailMsg_closing};
        sendmail(emailAddress,emailSubject,emailMessage);
    end
    [endTime,unit] = getEndTime(startTime);
    fprintf('OK (%.2f %s)\n',endTime,unit);
end

end