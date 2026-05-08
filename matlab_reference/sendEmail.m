function [] = sendEmail(subjectName,emailAddress,endTime_cell,emailFiles,test)
%% Function
if ~isempty(emailAddress)
    fprintf('Sending email...');
    startTime = tic;

    % Subject
    emailSubject = sprintf('MATLAB Stimulation: %s %s',subjectName,test);

    % Email name
    atSign = find(emailAddress == '@');
    emailName_idx = atSign - 1;
    emailName = emailAddress(1:emailName_idx);

    % Email message
    emailMsg_greeting = sprintf('Hello %s,',emailName);
    if iscell(endTime_cell) 
        endTime = endTime_cell{1};
        unit = endTime_cell{2};
    else
        endTime_s = endTime_cell;
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
    end
    emailMsg_1 = sprintf('Experiment completed  at %.2f %s.',endTime,unit);
    emailMsg_2 = 'The file(s) from the experiment is attached to this message.';
    emailMsg_closing = '-The Neural Interfaces Lab';

    % Email attachments
    % if ~iscell(emailFiles)
    %     emailFiles_0_idx = find(emailFiles == 0);
    %     emailFiles(emailFiles_0_idx) = []; %#ok<FNDSB>
    % end
    emailAttachments = cellstr(emailFiles);

    % Send
    if ~isempty(emailFiles)
        emailMessage = sprintf('%s\n\n%s\n%s\n\n%s',...
            emailMsg_greeting,...
            emailMsg_1,...
            emailMsg_2,...
            emailMsg_closing);
        try
            sendmail(...
                emailAddress,...
                emailSubject,...
                emailMessage,...
                emailAttachments);
        catch
            emailAttachments_new = emailAttachments(1:end-1);
            sendmail(...
                emailAddress,...
                emailSubject,...
                emailMessage,...
                emailAttachments_new);
        end
    else
        emailMessage = sprintf('%s\n\n%s\n\n%s',...
            emailMsg_greeting,...
            emailMsg_1,...
            emailMsg_closing);
        sendmail(...
            emailAddress,...
            emailSubject,...
            emailMessage);
    end
    [endTime,unit] = getEndTime(startTime);
    fprintf('OK (%.3f %s)\n',endTime,unit);
end

end