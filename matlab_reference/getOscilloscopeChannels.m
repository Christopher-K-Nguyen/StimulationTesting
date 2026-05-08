function [File,isQuit] = getOscilloscopeChannels(File)
%% Constants
% Buttons
BUTTON_CONFIRM = 'Confirm'; % confirm button
BUTTON_TRY = 'Start Over';  % start over button
BUTTON_CANCEL = 'Cancel';   % cancel button
DIMS = [1 100];
% Options
opts.Default = BUTTON_CONFIRM;  % option dedault
opts.Interpreter = 'tex';   % option LaTeX

%% Variables
numOfDevices = length(File.Oscilloscope);
isTPS = false;
isQuit = false;

%% Channels
for deviceNum = 1:numOfDevices
    make = File.Oscilloscope(deviceNum).Make;
    model = File.Oscilloscope(deviceNum).Model;
    resourceName = File.Oscilloscope(deviceNum).Resource;
    isUSB = contains2(resourceName,'usb');
    fprintf('Edit %s %s channels...\n',make,model);
    confirmChannels = false;
    while ~confirmChannels
        fprintf('\tSelect additional oscilloscope channels...');
        switch deviceNum
            case 1
                channelSelect_cell = {'CH1';'CH2'};
                addChannel_cell = {'';'CH3';'CH4'};
                promptChannels = 'Select additional oscilloscope channels.';     % instruction
                titleChannels = 'Additional Oscilloscope Channel Selection';
            case 2
                channelSelect_cell = {};
                addChannel_cell = {'CH1';'CH2';'CH3';'CH4'};
                promptChannels = 'Select oscilloscope measurement channels.';     % instruction
                titleChannels = 'Oscilloscope Measurement Channel Selection';
        end

        % Dialog box
        if isUSB
            if numOfDevices == 1
                initialValue = 2;
            else
                initialValue = 1;
            end
            [list_idx,~] = listdlg(...	% list dialog
                'PromptString',promptChannels,... % list prompts
                'ListString',addChannel_cell,...    % list
                'Name',titleChannels,...                 % list title
                'ListSize',[220 60], ....
                'SelectionMode','multiple', ....
                'InitialValue',initialValue);
        else
            [list_idx,~] = listdlg(...	% list dialog
                'PromptString',promptChannels,... % list prompts
                'ListString',addChannel_cell,...    % list
                'Name',titleChannels,...                 % list title
                'ListSize',[220 60], ....
                'SelectionMode','multiple', ....
                'InitialValue',[1 2]);           
        end
        
        % Collect input
        if isempty(list_idx)           % cancel detected
            fprintf('\nQuitting...'); % quitting
            isQuit = true;
            break;                     % exit program
        end

        % Confirm oscilloscope selection
        % Format questions
%         list_len = length(list_idx);
%         if list_len > 1 && ismember(1,list_idx)
%             list_idx(1) = [];
%         end
        if list_idx == 1 && numOfDevices == 1 && ~isTPS
            numOfAddChannels = 0;
        else
            numOfAddChannels = length(list_idx);
        end
        if numOfAddChannels > 0
            addChannel = addChannel_cell(list_idx);
            channelSelect_cell_alloc = [channelSelect_cell;addChannel];
            channelSelect_cell = channelSelect_cell_alloc;
        else
            addChannel_cell = {};
        end
        if ~isempty(addChannel_cell)
            channel_use = strjoin(addChannel,', ');
        else
            channel_use = 'NONE';
        end
        channelPrompt = sprintf('Additional oscilloscope channels: {\\bf%s}',channel_use);
        % Question box
        questChannels = questdlg(...                      % question dialog
            channelPrompt,...                       % question prompts
            'Confirm Additional Oscilloscope Channel Selection',...                        % question title
            BUTTON_CONFIRM,BUTTON_TRY,BUTTON_CANCEL,... % buttons
            opts);                                      % dialog options
        % Confirmation
        switch questChannels                              % apply choice
            case BUTTON_CONFIRM                         % check confirmation
                confirmChannels = true;               % confirm info
                fprintf('%s\n',channel_use);  % info confirmed
            case BUTTON_TRY                             % try again
                confirmChannels = false;               % trying again
                fprintf('\nTrying agin...\n\n');          % starting over
            case BUTTON_CANCEL                          % quit
                fprintf('\nQuitting...');             % quitting
                isQuit = true;
                break;                                 % exit program
            otherwise                                   % cancel
                fprintf('\nQuitting...');             % quitting
                isQuit = true;
                break;                                 % exit program
        end
    end
    if isQuit
        return;                                 % exit program
    end

    %% Channel Names
    confirmChannelName = false;
    numOfChannels = length(channelSelect_cell);
    inputChannelList = cell(1,numOfChannels);
    for channel_idx = 1:numOfChannels
        channelName = channelSelect_cell{channel_idx};
        if numOfDevices == 1 || (numOfDevices == 2 && deviceNum == 1)
            switch channel_idx
                case 1
                    inputChannelList{channel_idx} = sprintf( ...
                        '%s {\\bf(Should be Voltage)}',channelName);
                case 2
                    inputChannelList{channel_idx} = sprintf( ...
                        '%s {\\bf(Should be Current)}',channelName);
                otherwise
                    inputChannelList{channel_idx} = channelName;
            end
        else
            inputChannelList{channel_idx} = channelName;
        end
    end

    switch numOfDevices
        case 1
            model = File.Oscilloscope(deviceNum).Model;
            isTPS = contains2(model,'tps');
            switch numOfChannels
                case 1
                    inputDefault = {'Voltage (V)'};
                case 2
                    inputDefault = {'Voltage (V)','Current (uA)'};
                case 3
                    if isTPS
                        inputDefault = {'Voltage (V)','Current (uA)','Active (V)'};
                    else
                        inputDefault = {'Voltage (V)','Current (uA)','Return (V)'};
                    end
                case 4
                    inputDefault = {'Voltage (V)','Current (uA)','Active (V)','Return (V)'};
            end
        case 2
            switch deviceNum
                case 1
                    switch numOfChannels
                        case 1
                            inputDefault = {'Voltage (V)'};
                        case 2
                            inputDefault = {'Voltage (V)','Current (uA)'};
                        case 3
                            inputDefault = {'Voltage (V)','Current (uA)','Differential (V)'};
                        case 4
                            inputDefault = {'Voltage (V)','Current (uA)','Differential 1 (V)','Differential 2 (V)'};
                    end
                case 2
                    switch numOfChannels
                        case 1
                            inputDefault = {'Active (V)'};
                        case 2
                            inputDefault = {'Active (V)','Return (V)'};
                        case 3
                            inputDefault = {'Active (V)','Return 1 (V)','Return 2 (V)'};
                        case 4
                            inputDefault = {'Active (V)','Return 1 (V)','Return 2 (V)','Return 3 (V)'};
                    end
            end
    end

    while ~confirmChannelName
        fprintf('\t');
        fprintf('Enter oscilloscope channel names and include units for plot...');
        channelName_cell = inputdlg( ...
            inputChannelList, ...
            'Oscilloscope Channel Names with Units', ...
            DIMS, ...
            inputDefault, ...
            opts);
        if isempty(channelName_cell)
            fprintf('\nQuitting...');             % quitting
            isQuit = true;
            break;                                 % exit program
        end
        inputDefault = channelName_cell;
        questList = cell(1,numOfChannels);
        for channel_idx = 1:numOfChannels
            channel = channelSelect_cell{channel_idx};
            inputName = channelName_cell{channel_idx};
            if deviceNum == 1 && channel_idx == 2
                if contains2(inputName,'curr')
                    questList{channel_idx} = sprintf('%s: {\\bf%s}', ...
                        channel,inputName);
                else
                    questList{channel_idx} = sprintf( ...
                        '%s {\\bf(Should be in Current)}: {\\bf%s}', ...
                        channel,inputName);
                end
            else
                questList{channel_idx} = sprintf('%s: {\\bf%s}', ...
                    channel,inputName);
            end
        end
        questNames = questdlg( ...
            questList, ...
            'Confirm Oscilloscope Channel Names with Units', ...
            BUTTON_CONFIRM,BUTTON_TRY,BUTTON_CANCEL,... % buttons
            opts);
        % Confirmation
        switch questNames                              % apply choice
            case BUTTON_CONFIRM                         % check confirmation
                confirmChannelName = true;               % confirm info
                channelName_char = strjoin(channelName_cell,', ');
                fields_cell = cell(numOfChannels,1);
                units_cell = cell(numOfChannels,1);
                for scopeChannelNum = 1:numOfChannels
                    channelName = channelName_cell{scopeChannelNum};
                    space_idx = strfind2(channelName,' ',1);
                    start_idx = strfind(channelName,'(') + 1;
                    end_idx = strfind(channelName,')') - 1;
                    fields_cell{scopeChannelNum} = channelName(1:space_idx-1);
                    units_cell{scopeChannelNum} = channelName(start_idx:end_idx);
                end
                fprintf('%s\n',channelName_char);  % info confirmed
            case BUTTON_TRY                             % try again
                confirmChannelName = false;               % trying again
                fprintf('\nTrying agin...\n\n');          % starting over
            case BUTTON_CANCEL                          % quit
                fprintf('\nQuitting...');             % quitting
                isQuit = true;
                break;                                 % exit program
            otherwise                                   % cancel
                fprintf('\nQuitting...');             % quitting
                isQuit = true;
                break;                                 % exit program
        end
    end
    if isQuit
        return;                                 % exit program
    end

    % hasVoltage = contains2(channelName_cell,{'volt'});
    % hasReturn = contains2(channelName_cell,{'ret','count'});
    % hasActive = contains2(channelName_cell,{'act','work'});
    % hasAltActive = ~hasActive && hasVoltage && hasReturn;
    % if numOfDevices == 1 && hasAltActive
    %     hasMATH = input('Enter 1 if you want MATH for alternative active channel, otherwise 0: ');
    %     if hasMATH
    %         numOfChannels = numOfChannels + 1;
    %         channelSelect_cell{numOfChannels} = 'MATH';
    %         channelName_cell{numOfChannels} = 'Active (V)';
    %         fields_cell{numOfChannels} = 'Active';
    %         units_cell{numOfChannels} = 'V';
    %     end
    % end

    %% Store
    File.Oscilloscope(deviceNum).NumberOfChannels = numOfChannels;
    File.Oscilloscope(deviceNum).Channels = channelSelect_cell;
    File.Oscilloscope(deviceNum).ChannelNames = channelName_cell;
    File.Oscilloscope(deviceNum).Fields = fields_cell;
    File.Oscilloscope(deviceNum).Units = units_cell;
end

end