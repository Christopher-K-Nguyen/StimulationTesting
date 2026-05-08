function [isAgain,isStop] ...
    = changingChannel( ...
    File, ...
    startTime,startChannelTime)
%% Constants
BUTTON_OK = 'OK';
BUTTON_TRY = 'Try Again';
BUTTON_QUIT = 'QUIT';

%% Variables
isAgain = false;
isStop = false;
[endChannelTime,unit] = getEndTime(startChannelTime);

% Device
deviceType = File.Parameters.Device;
isOther = contains2(deviceType,{'oth','test'});

% Channels
channel_arr = [File.Data(:).ActiveChannel];
groupNum = length(channel_arr);
channelNum = channel_arr(groupNum);
channelGroup_mat = File.Test.Groups;
[numOfGroups,numOfChannelsUsed] = size(channelGroup_mat);
channelReturn_arr = channelGroup_mat(groupNum,2:numOfChannelsUsed);
numOfReturns = length(channelReturn_arr);

% Configuration
configID = File.Parameters.Configuration.ID;
isMP = contains2(configID,'MP');
isPTP = contains2(configID,'PTP');
prompt1 = sprintf('Channel %d completed in %.2f %s\n',channelNum,endChannelTime,unit);	% elapsed time

%% Function
if groupNum < numOfGroups          	% more channels
    PS_StopStimChannel(1,channelNum); % stop stimulating channel
    fprintf(prompt1);
    nextChannelNum = channelGroup_mat(groupNum+1,1);        % next channel number
    if isMP
        fprintf('Next is Channel %d.\n',nextChannelNum);
    else
        if numOfReturns > 1
            channelReturn_cell = char2(channelReturn_arr);
            channelReturn_char = strjoin(channelReturn_cell,', ');
        else
            channelReturn_char = num2str(channelReturn_arr);
        end
        if isPTP
            counterElectrode = File.Parameters.CounterElectrode.Type;
            channelReturn_use = [channelReturn_char ', ' counterElectrode];
        else
            channelReturn_use = channelReturn_char;
        end
        fprintf('Next is Channel %d versus Channel %s.\n',nextChannelNum,channelReturn_use);
    end
    isAgain = false;
    isStop = false;
    if isOther                 	% connected to single channel
        promptDone = {
            prompt1, ...
            prompt2, ...
            'Do you want to move on?'};
        questDone = questdlg( ...
            promptDone, ...
            'DONE', ...
            BUTTON_OK,BUTTON_QUIT);
        switch questDone
            case BUTTON_OK
                isAgain = false;
                isStop = false;
            case BUTTON_TRY
                isAgain = true;
                isStop = false;
            case BUTTON_QUIT
                isStop = true;
            otherwise
                isStop = true;
        end
    end
%     fprintf('Moving onto next channel...\n\n');% automatically change channel
%     fprintf('\n');
else % on last channel
    fprintf(prompt1);
    [endTime,unit] = getEndTime(startTime);
    fprintf('\nAll stimulations completed in %.2f %s\n',endTime,unit);
end

end