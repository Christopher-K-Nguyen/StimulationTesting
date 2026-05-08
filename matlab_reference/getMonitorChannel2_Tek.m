function  File = getMonitorChannel2_Tek(File)
%% Constants
% Buttons
BUTTON_CONFIRM = 'Confirm';
BUTTON_CANCEL = 'Cancel';
BUTTON_TRY = 'Try Again';
opts.Interpreter = 'tex';   % option LaTeX
DIMS = [220 80];

%% Variables
numOfDevices = length(File.Oscilloscope);
capture_arr = [File.Data.Index];
captureNum = length(capture_arr);
monitorChannel2_cell = cell(numOfDevices,1);
cursorStruct = struct( ...
    'Name','', ...
    'Time','', ...
    'Value','');

%% Function
for deviceNum = 1:numOfDevices
    make = File.Oscilloscope(deviceNum).Make;
    model = File.Oscilloscope(deviceNum).Model;
    channelSelect_cell = File.Oscilloscope(deviceNum).Channels;
    channelName_cell = File.Oscilloscope(deviceNum).ChannelNames;
    numOfChannels = File.Oscilloscope(deviceNum).NumberOfChannels;

    switch deviceNum
        case 1
            monitorChannel_idx = 1;
        case 2
            monitorChannel_idx = 1:numOfChannels;
    end

    confirmMonitorChannel = false;    
    while ~confirmMonitorChannel
        fprintf('Select %s %s monitor channel...',make,model);
        % Dialog box
        promptMonitorChannel = sprintf('Select %s %s monitor channel.',make,model);     % instruction
        titleMonitorChannel = sprintf('%s %s Monitor Channel Selection',make,model);
        [monitorChannel_idx,~] = listdlg(...	% list dialog
            'PromptString',promptMonitorChannel,... % list prompts
            'ListString',channelSelect_cell,...    % list
            'Name',titleMonitorChannel,...                 % list title
            'ListSize',DIMS, ....
            'InitialValue',monitorChannel_idx, ...
            'SelectionMode','multiple');
        % Collect input
        if isempty(monitorChannel_idx)  % cancel detected
            break;                      % exit program
        end
        numOfMonitor = length(monitorChannel_idx);
        monitorChannel_cell = channelSelect_cell(monitorChannel_idx);

        % Confirm oscilloscope selection
        % Format questions
        if numOfMonitor > 1
            monitorChannel2_cell{deviceNum} = cell(numOfMonitor,1);
            for idx = monitorChannel_idx
                monitorChannel = monitorChannel_cell{idx};
                monitorChannelName = channelName_cell{idx};
                monitorChannel_char = sprintf('%s %s',monitorChannel,monitorChannelName);
                monitorChannel2_cell{deviceNum}{idx} = monitorChannel_char;
            end
            deviceMonitor_cell = monitorChannel2_cell{deviceNum};
            monitorChannel_use = sprintf('%s, ',deviceMonitor_cell{:});
            monitorChannel_len = length(monitorChannel_use);
            monitorChannel_use(monitorChannel_len-2:monitorChannel_len) = [];
            channelPrompt = sprintf('Monitor Channel: {\\bf%s}',monitorChannel_use);
        else
            monitorChannel = monitorChannel_cell{1};
            monitorChannel2_cell{deviceNum} = monitorChannel_cell;
            monitorChannelName = channelName_cell{monitorChannel_idx};
            monitorChannel_use = sprintf('%s %s',monitorChannel,monitorChannelName);
            channelPrompt = sprintf('Monitor Channel: {\\bf%s}',monitorChannel_use);
        end
        % Question box
        opts.Default = BUTTON_CONFIRM;
        questTitle = sprintf('Confirm %s %s Monitor Channel Selection',make,model);
        questMonitorChannel = questdlg(...                      % question dialog
            channelPrompt,...                       % question prompts
            questTitle,...% question title
            BUTTON_CONFIRM,BUTTON_TRY,BUTTON_CANCEL,... % buttons
            opts);                                      % dialog options
        % Confirmation
        switch questMonitorChannel                              % apply choice
            case BUTTON_CONFIRM                         % check confirmation
                confirmMonitorChannel = true;               % confirm info
                File.Data(captureNum).Monitor(deviceNum).Channel = monitorChannel_cell;
                for monitor_idx = 1:numOfMonitors
                    monitorChannel = monitorChannel_cell{monitor_idx};
                    File.Data(captureNum).Monitor(deviceNum).(monitorChannel) = cursorStruct;
                end
                fprintf('%s\n',monitorChannel_use);  % info confirmed
            case BUTTON_TRY                             % try again
                confirmMonitorChannel = false;               % trying again
                fprintf('\nTrying agin...\n\n');          % starting over
            case BUTTON_CANCEL                          % quit
                break;                                 % exit program
            otherwise                                   % cancel
                break;                                 % exit program
        end
    end
end

end