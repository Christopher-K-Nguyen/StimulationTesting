function isQuit = startStimulation(File,varargin)
%% Constants
% Buttons
BUTTON_QUIT = 'Quit';
BUTTON_TRY = 'Start Over';  % start over button
TITLE_ERROR = 'ERROR';
% Options
opts.Default = 'yes';       % option dedault
opts.Interpreter = 'tex';   % option LaTeX

%% Variables
isQuit = false;

% Experiment
expType = File.Test.Experiment;
isPulsing = contains2(expType,{'SP','LP'});

% Channels
if isPulsing
    testChannel_arr = File.Parameters.Channels.Test;
    channelNum = testChannel_arr;
else
    channel_arr = [File.Data(:).ActiveChannel];
    groupNum = length(channel_arr);
    channelNum = channel_arr(groupNum);
end
plexonChannel_arr = File.Parameters.Channels.Plexon;

% Return Channels
if ~isempty(varargin)
    returnChannel_arr = varargin{1};
else
    returnChannel_arr = [];
end
numOfReturns = length(returnChannel_arr);

% Configuration
configID = File.Parameters.Configuration.ID;
isCG = contains2(configID,'CG');

%% Stimulation
err = 1;                                          	% initialize errors
while err ~= 0                                      % start stimulating channel
    fprintf('Channel %d stimulation...',channelNum);
    startTime = tic;
    if isCG
        plexonChannel = plexonChannel_arr(channelNum);
        err = PS_StartStimChannel(1,plexonChannel);
    else
%         testChannel_arr = File.Parameters.Channels.Test;
%         channelStim_arr = testChannel_arr;
%         channelStim_arr(returnChannel_arr) = [];
%         numOfReturns = length(returnChannel_arr);
%         numOfStimChannels = length(channelStim_arr);
%         err_arr = zeros(1,numOfStimChannels);
%         for channel_idx = 1:numOfStimChannels
%             channelStim = channelStim_arr(channel_idx);
%             plexonChannel = plexonChannel_arr(channelStim);
%             err = PS_StartStimChannel(1,plexonChannel);
%             if err ~= 0
%                 break;
%             end
%         end
%         err = all(err_arr == 0);
        err = PS_StartStimAllChannels(1);  % get errors
    end
    switch err      % getting errors
        case 0                      % no errors
            [endTime,unit] = getEndTime(startTime);
            fprintf('ON \t\t(%.2f %s)\n',endTime,unit);   % channel stimulating
            % if numOfReturns > 0
            %     startTime = tic;
            %     fprintf('\tTurning off Channel: ');
            %     for return_idx = 1:numOfReturns
            %         channelReturn = returnChannel_arr(return_idx);
            %         if return_idx > 1
            %             fprintf(', %d',channelReturn);
            %         else
            %             fprintf('%d',channelReturn);
            %         end
            %         plexonReturn = plexonChannel_arr(channelReturn);
            %         isStimOn = true;
            %         while isStimOn
            %             PS_StopStimChannel(1,plexonReturn);
            %             [isStimOn,~] = PS_ChannelStimStarted(1,plexonReturn);
            %         end
            %     end
            %     [endTime,unit] = getEndTime(startTime);
            %     fprintf(' (%.2f %s)\n',endTime,unit);   % channel stimulating
            % end
        case 1                                                                  % errors found
            msg = 'ERROR STARTING STIMULATION';                                 % error starting stimulation
        case 4                                                                  % errors found
            msg = 'WRONG TRIGGER MODE ON STIMULATOR';                           % wrong trigger mode
        case -1                                                                 % errors found
            msg = 'INVALID ARGUMENT(S)';                                        % invalid argument(s)
            
    end
    if err ~= 0
        quest = questdlg(msg,TITLE_ERROR,BUTTON_TRY,BUTTON_QUIT,opts);
        switch quest                        % apply choice
            case BUTTON_QUIT            	% quit
                fprintf('Quitting...\n\n');	% quitting
                isQuit = true;
                break;                     % exit program
            case BUTTON_TRY               	% try again
                fprintf('Trying again...\n\n');% trying again
%                 try
%                     PS_InitAllStim();
%                     pause(5);
%                 catch
%                 end
            otherwise                       % cancel
                fprintf('Quitting...\n\n');	% quitting
                isQuit = true;
                break;                     % exit program
        end
    end
end
if isQuit
    return;
end

end