function setTriggerLevelExt(File,deviceNum)
%% Variables
% subjectSelect = File.Subject;
oscilloscope = File.Oscilloscope(deviceNum).Object;
model = File.Oscilloscope(deviceNum).Model;
isTPS = contains2(model,'tps');

%% Function
% Adjust trigger level
fprintf('Setting trigger Level...');
startTime = tic;
triggerLevel = 0.2;
triggerLevel_use = sprintf('TRIGger:MAIn:LEVel %.2e',triggerLevel);
if isTPS
    triggerLevel_check = [];
    %     triggerLevel_use = 'TRIGger:MAIn SETLevel';
    while ~isequal(triggerLevel_check,triggerLevel)
        fprintf(oscilloscope,triggerLevel_use);
        fprintf(oscilloscope,'TRIGger:MAIn:LEVel?');
        triggerLevel_raw = fgetl2(oscilloscope);
        triggerLevel_check = str2double(triggerLevel_raw);
        if toc(startTime) > 1
            break;
        end
    end
else
    fprintf(oscilloscope,triggerLevel_use);
end
[endTime,unit] = getEndTime(startTime);
fprintf('OK \t\t(%.2f %s)\n',endTime,unit);


% % Set trigger monitor
% status_query = sprintf('SELect:%s?',scopeChannel);
% fprintf(oscilloscope,status_use);
% fprintf(oscilloscope,status_query);
% status_check = fgetl2(oscilloscope);
% fprintf('Setting trigger monitor...');
% startTime = tic;
% 
% [endTime,unit] = getEndTime(startTime);
% fprintf('OK \t\t(%.2f %s)\n',endTime,unit);

end